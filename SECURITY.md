# Security Policy

## Reporting a vulnerability

Please report security issues privately to **akj123429@gmail.com** (or via GitHub's
"Report a vulnerability" / private security advisories), not in a public issue. Include
steps to reproduce and impact. We'll acknowledge and work on a fix; please allow
reasonable time before public disclosure.

## How the frontend is hardened

The site is static (GitHub Pages) with no backend, which removes whole classes of
server-side risk. On top of that:

- **Content Security Policy.** Scripts load only from the site's own origin — no inline
  JavaScript and no third-party CDNs — and the browser may only `connect` to
  `api.github.com`. See the `<meta http-equiv="Content-Security-Policy">` in
  `site/_layouts/default.html`. (Set `frame-ancestors` as a real HTTP header at your
  host for clickjacking protection, since that directive is ignored in a meta tag.)
- **No `innerHTML` for data.** Contributed database content (including signal and frame
  names that appear in lint warnings) is untrusted and is inserted only via `textContent`
  and DOM APIs, so markup inside a file can never execute as HTML (stored-XSS defense).
- **Inert, escaped catalog data.** The catalog is embedded as HTML-escaped
  `type="application/json"` (not executable) and parsed with `JSON.parse`, preventing
  `</script>` breakout from a malicious file name.
- **Escaped templating.** All Liquid output is passed through `| escape`.
- **Validated upload inputs.** Manufacturer/model/bus must match a safe slug pattern,
  file names are restricted to the allowed extensions, and the fork is checked as
  `owner/repo` — blocking path traversal and URL injection into GitHub API calls. Values
  placed in API paths are URL-encoded.
- **Token handling.** The GitHub token is used only against `api.github.com`, is never
  logged or sent elsewhere, and is stored (in `localStorage`) only if you opt in; it is
  removed when you opt out. Treat it like a password and prefer a fine-grained token
  limited to your fork.

## No third-party scripts

The frontend loads no external JavaScript, so there is no supply-chain surface from CDNs.
If you add any, pin an exact version and use Subresource Integrity, and update the CSP.
