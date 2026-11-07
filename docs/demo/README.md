# Agent Autofill demo

Open [`index.html`](index.html) directly in a browser, or serve the repository
locally:

```bash
python3 -m http.server 8000 --directory docs/demo --bind 127.0.0.1
open http://127.0.0.1:8000/
```

The page is a dependency-free, synthetic product tour. It does not call the
broker, read a file, request a permission, or send a network request. It is
safe to record as a short walkthrough:

1. Open the vault and show the value-free inventory.
2. Preview a request for `example.test` and switch between direct and
   extension delivery.
3. Approve the exact origin, purpose, fields, and expiry.
4. Deliver selected fields or the one-time origin-bound handle.
5. Revoke the grant and show that the next request is blocked.

Every displayed value is synthetic. Do not replace the demo data with real
credentials, OTPs, tokens, or financial values.
