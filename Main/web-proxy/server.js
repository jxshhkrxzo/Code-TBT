// server.js - Custom Web Proxy with HTML/CSS asset rewriting
// Run: npm install; then npm start -> http://localhost:3000
const express = require('express');
const cors = require('cors');
const morgan = require('morgan');
const cheerio = require('cheerio');
const path = require('path');
const { createProxyMiddleware } = require('http-proxy-middleware');

const app = express();
const PORT = process.env.PORT || 3000;

app.use(cors());
app.use(morgan('dev'));
app.use(express.json({ limit: '2mb' }));
app.use(express.urlencoded({ extended: true }));

// Serve frontend
app.use(express.static(path.join(__dirname, 'public')));

// ---------- Helpers ----------

// Basic SSRF guard: block localhost / private ranges
function isBlockedHost(hostname) {
  const h = hostname.toLowerCase();
  return (
    h === 'localhost' ||
    h.endsWith('.localhost') ||
    h === '[::1]' ||
    h === '::1' ||
    h.startsWith('127.') ||
    h.startsWith('10.') ||
    h.startsWith('192.168.') ||
    h.startsWith('169.254.') ||
    /^172\.(1[6-9]|2\d|3[01])\./.test(h) ||
    h === '0.0.0.0'
  );
}

function normalizeTargetUrl(input) {
  if (!input) return null;
  input = input.trim();
  // Add protocol if missing
  if (!/^[a-zA-Z][a-zA-Z\d+\-.]*:\/\//.test(input)) {
    input = 'https://' + input;
  }
  try {
    const u = new URL(input);
    if (u.protocol !== 'http:' && u.protocol !== 'https:') return null;
    if (isBlockedHost(u.hostname)) return null;
    return u;
  } catch {
    return null;
  }
}

function toProxyUrl(absoluteUrl) {
  return `/proxy?url=${encodeURIComponent(absoluteUrl)}`;
}

function rewriteUrlAttribute(value, base) {
  if (!value || !value.trim()) return value;
  const v = value.trim();
  // Skip anchors, javascript:, mailto:, tel:, data:, blob:
  if (
    v.startsWith('#') ||
    v.startsWith('javascript:') ||
    v.startsWith('mailto:') ||
    v.startsWith('tel:') ||
    v.startsWith('data:') ||
    v.startsWith('blob:')
  ) return v;
  try {
    const abs = new URL(v, base).href;
    const parsed = new URL(abs);
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return v;
    if (isBlockedHost(parsed.hostname)) return v;
    return toProxyUrl(abs);
  } catch {
    return v;
  }
}

// Rewrite CSS url(...) + @import references
function rewriteCss(cssText, base) {
  if (!cssText) return cssText;
  let out = cssText.replace(/url\(\s*(['"]?)([^'")]+)\1\s*\)/g, (match, quote, url) => {
    if (url.startsWith('data:') || url.startsWith('blob:') || url.startsWith('#')) return match;
    const rewritten = rewriteUrlAttribute(url, base);
    return `url(${quote}${rewritten}${quote})`;
  });
  // @import "foo.css" and @import 'foo.css' (Forge styles_light.css uses Google Fonts this way)
  out = out.replace(/@import\s+(["'])((?:(?!\1).)+)\1/g, (match, quote, url) => {
    if (url.startsWith('data:') || url.startsWith('blob:')) return match;
    return `@import ${quote}${rewriteUrlAttribute(url, base)}${quote}`;
  });
  return out;
}

// Strip headers that break iframing / proxying
function stripProblemHeaders(headers) {
  const drop = new Set([
    'content-security-policy',
    'content-security-policy-report-only',
    'x-frame-options',
    'x-powered-by',
    'transfer-encoding',
    'connection',
    'content-encoding', // we decode/re-encode via fetch buffer
    'content-length',   // recomputed by express
  ]);
  const out = {};
  for (const [k, v] of Object.entries(headers)) {
    if (!drop.has(k.toLowerCase())) out[k] = v;
  }
  return out;
}

// ---------- Main proxy endpoint: /proxy?url=... ----------
// Handles HTML rewriting + passes through assets (images/js/css/etc.)
app.use('/proxy', async (req, res) => {
  const target = normalizeTargetUrl(req.query.url);
  if (!target) {
    return res.status(400).send(
      'Missing or invalid ?url=. Use http(s) URL. Example: /proxy?url=https://example.com'
    );
  }

  const targetHref = target.href;

  try {
    // Forward client headers, but fix Host/Origin/Referer (changeOrigin)
    const forwardHeaders = {
      'user-agent':
        req.headers['user-agent'] ||
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36',
      accept: req.headers['accept'] || '*/*',
      'accept-language': req.headers['accept-language'] || 'en-US,en;q=0.9',
    };
    // Forward cookies if present (basic support)
    if (req.headers.cookie) forwardHeaders.cookie = req.headers.cookie;

    const fetchOptions = {
      method: req.method === 'GET' || req.method === 'HEAD' ? 'GET' : req.method,
      headers: {
        ...forwardHeaders,
        Host: target.host,
        Origin: target.origin,
        Referer: target.origin + '/',
      },
      redirect: 'manual',
    };

    // Forward body for POST/PUT/PATCH
    if (!['GET', 'HEAD'].includes(req.method)) {
      // Collect raw body
      const chunks = [];
      for await (const chunk of req) chunks.push(chunk);
      if (chunks.length) fetchOptions.body = Buffer.concat(chunks);
      if (req.headers['content-type']) {
        fetchOptions.headers['content-type'] = req.headers['content-type'];
      }
    }

    const upstream = await fetch(targetHref, fetchOptions);

    // Handle redirects: rewrite Location -> /proxy?url=...
    if ([301, 302, 303, 307, 308].includes(upstream.status)) {
      const loc = upstream.headers.get('location');
      if (loc) {
        try {
          const absLoc = new URL(loc, targetHref).href;
          return res.redirect(302, toProxyUrl(absLoc));
        } catch {
          return res.redirect(302, loc);
        }
      }
    }

    const contentType = upstream.headers.get('content-type') || '';

    // Forward Set-Cookie (basic): strip Domain so cookie sticks to localhost
    const setCookies = upstream.headers.getSetCookie
      ? upstream.headers.getSetCookie()
      : upstream.headers.get('set-cookie')
        ? [upstream.headers.get('set-cookie')]
        : [];
    if (setCookies.length) {
      const rewritten = setCookies.map((c) =>
        c
          .split(';')
          .filter((part) => !/^\s*domain=/i.test(part))
          .join(';')
      );
      res.setHeader('Set-Cookie', rewritten);
    }

    // Copy safe headers
    const rawHeaders = {};
    upstream.headers.forEach((v, k) => (rawHeaders[k] = v));
    const safeHeaders = stripProblemHeaders(rawHeaders);
    for (const [k, v] of Object.entries(safeHeaders)) {
      // Express sets content-type itself; skip here, set later
      if (k.toLowerCase() === 'content-type') continue;
      try { res.setHeader(k, v); } catch {}
    }

    const buffer = Buffer.from(await upstream.arrayBuffer());

    // 1) HTML -> rewrite asset links with cheerio
    if (contentType.includes('text/html')) {
      const html = buffer.toString('utf8');
      const $ = cheerio.load(html);

      // Rewrite attributes
      $('a[href], link[href]').each((_, el) => {
        const v = $(el).attr('href');
        if (v) $(el).attr('href', rewriteUrlAttribute(v, targetHref));
      });
      $('script[src], img[src], source[src], video[src], audio[src], iframe[src], embed[src], track[src]')
        .each((_, el) => {
          const v = $(el).attr('src');
          if (v) $(el).attr('src', rewriteUrlAttribute(v, targetHref));
        });
      $('img[srcset], source[srcset]').each((_, el) => {
        const v = $(el).attr('srcset');
        if (v) {
          const parts = v.split(',').map((p) => {
            const [u, ...desc] = p.trim().split(/\s+/);
            return [rewriteUrlAttribute(u, targetHref), ...desc].join(' ');
          });
          $(el).attr('srcset', parts.join(', '));
        }
      });
      $('form[action]').each((_, el) => {
        const v = $(el).attr('action');
        if (v) $(el).attr('action', rewriteUrlAttribute(v, targetHref));
      });
      // Lazy-load / media attributes Forge and others use
      $('[data-src], [data-href], video[poster], img[data-srcset]').each((_, el) => {
        for (const attr of ['data-src', 'data-href', 'poster', 'data-srcset']) {
          const v = $(el).attr(attr);
          if (v) $(el).attr(attr, rewriteUrlAttribute(v.split(' ')[0], targetHref));
        }
      });
      // Meta refresh redirects
      $('meta[http-equiv="refresh" i]').each((_, el) => {
        const v = $(el).attr('content');
        if (v) {
          $(el).attr('content', v.replace(/url\s*=\s*(.+)/i, (m, u) => {
            return 'url=' + rewriteUrlAttribute(u.trim().replace(/^['"]|['"]$/g, ''), targetHref);
          }));
        }
      });
      // Inline style="background: url(...)"
      $('[style]').each((_, el) => {
        const v = $(el).attr('style');
        if (v && v.includes('url(')) $(el).attr('style', rewriteCss(v, targetHref));
      });
      // <style> blocks
      $('style').each((_, el) => {
        $(el).text(rewriteCss($(el).html(), targetHref));
      });

      // Use directory URL (not just origin) so relative JS-created URLs resolve correctly.
      // e.g. index_1.21.html on /net/minecraftforge/forge/ must stay in that folder.
      $('base').remove();
      const baseHref = new URL('.', targetHref).href;
      $('head').prepend(`<base href="${baseHref}">`);

      // Inject hook to catch client-side navigation/fetch so SPA links stay in proxy
      $('body').append(`<script>
(function(){
  const BASE = ${JSON.stringify(targetHref)};
  const BYPASS = /(adfoc\\.us|maven\\.forgeforge|maven\\.minecraftforge|googletagmanager\\.com|lngtd\\.com)/;
  const toProxy = (u) => {
    try {
      if (!u) return u;
      const s = String(u);
      if (s.startsWith('/proxy?url=')) return s;
      if (s.startsWith('#') || s.startsWith('javascript:') || s.startsWith('mailto:') || s.startsWith('data:') || s.startsWith('blob:')) return s;
      const abs = new URL(s, BASE).href;
      if (BYPASS.test(abs)) return abs; // ad-wall / jar downloads: let browser handle directly
      return '/proxy?url=' + encodeURIComponent(abs);
    } catch(e){ return u; }
  };
  // Keep top address bar in sync
  try { window.parent.postMessage({ type:'proxy-navigated', url: BASE }, '*'); } catch(e){}
  // Intercept clicks: ad/jar links open in new tab, others stay in iframe
  document.addEventListener('click', (e) => {
    const a = e.target && e.target.closest ? e.target.closest('a[href]') : null;
    if (!a) return;
    const href = a.getAttribute('href');
    if (!href || href.startsWith('#')) return; // let sidebar toggles (href="#") run their own JS
    let abs = href;
    try { abs = new URL(href, BASE).href; } catch(e){}
    if (BYPASS.test(abs)) {
      e.preventDefault();
      window.open(abs, '_blank', 'noopener');
      return;
    }
    if (a.target === '_blank') return;
    e.preventDefault();
    window.location.href = toProxy(href);
  });
  // Route dynamic fetch/XHR through proxy (Forge loads bits via JS)
  const _fetch = window.fetch;
  if (_fetch) window.fetch = (input, init) => {
    if (typeof input === 'string') input = toProxy(input);
    else if (input && input.url) {
      const proxied = toProxy(input.url);
      if (proxied !== input.url) input = new Request(proxied, input);
    }
    return _fetch(input, init);
  };
  const _open = XMLHttpRequest.prototype.open;
  XMLHttpRequest.prototype.open = function(m, u, ...rest) {
    try { u = toProxy(u); } catch(e){}
    return _open.call(this, m, u, ...rest);
  };
})();
</scr` + `ipt>`);

      res.setHeader('Content-Type', 'text/html; charset=utf-8');
      return res.status(upstream.status).send($.html());
    }

    // 2) CSS -> rewrite url(...)
    if (contentType.includes('text/css')) {
      const css = rewriteCss(buffer.toString('utf8'), targetHref);
      res.setHeader('Content-Type', contentType);
      return res.status(upstream.status).send(css);
    }

    // 3) Everything else (JS, images, fonts, JSON): pass through bytes
    res.setHeader('Content-Type', contentType || 'application/octet-stream');
    return res.status(upstream.status).send(buffer);
  } catch (err) {
    console.error('Proxy error for', targetHref, err.message);
    return res.status(502).send(`Proxy fetch failed: ${err.message}`);
  }
});

// ---------- Alternative full-fidelity proxy using http-proxy-middleware ----------
// Usage: /proxy-direct?url=https://example.com/path
// Demonstrates changeOrigin + cookie rewrite + header stripping.
app.use(
  '/proxy-direct',
  (req, res, next) => {
    const target = normalizeTargetUrl(req.query.url);
    if (!target) return res.status(400).send('Missing ?url= for /proxy-direct');
    req.proxyTarget = target.origin;
    // Strip /proxy-direct prefix, forward remainder path
    req.url = target.pathname + target.search;
    next();
  },
  createProxyMiddleware({
    changeOrigin: true,
    ws: true,
    logLevel: 'warn',
    cookieDomainRewrite: { '*': '' },
    router: (req) => req.proxyTarget,
    on: {
      proxyRes: (proxyRes) => {
        delete proxyRes.headers['content-security-policy'];
        delete proxyRes.headers['x-frame-options'];
        delete proxyRes.headers['content-security-policy-report-only'];
      },
    },
  })
);

app.get('/health', (_, res) => res.json({ ok: true }));

app.listen(PORT, () => {
  console.log(`Proxy running at http://localhost:${PORT}`);
  console.log(`Try: http://localhost:${PORT}/proxy?url=https://example.com`);
});
