"""Checks that a Postgres server's TLS certificate verifies against a CA file, the way n8n will check it.

    DATABASE_URL=postgresql://user:pass@host:5432/db python .github/scripts/check_db_cert.py n8n-leads/supabase/prod-ca-2021.crt

Opens a TLS connection to the host and port in DATABASE_URL (Postgres STARTTLS, through `openssl s_client`) and
verifies the certificate chain and the host name against the CA file only, not the system's CA store. Nothing
is sent after the handshake: no login, no query. Prints the result and the certificate's issuer, never the
host, user or password. Exit code 0 when the certificate verifies, 1 when it does not.
"""

import os
import re
import subprocess
import sys
from urllib.parse import urlsplit


def check(url: str, ca_file: str, timeout: int = 30) -> tuple[bool, str]:
    parts = urlsplit(url)
    host, port = parts.hostname, parts.port or 5432
    if not host:
        return False, "DATABASE_URL has no host"
    if not os.path.isfile(ca_file):
        return False, f"CA file not found: {ca_file}"
    cmd = ["openssl", "s_client", "-starttls", "postgres", "-connect", f"{host}:{port}", "-servername", host,
           "-CAfile", ca_file, "-no-CApath", "-no-CAstore", "-verify_return_error", "-verify_hostname", host]
    try:
        done = subprocess.run(cmd, input="", capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return False, f"no answer within {timeout} s"
    output = done.stdout + done.stderr
    code = re.search(r"Verify return code: (\d+) \(([^)]*)\)", output)
    issuer = re.search(r"issuer=(.*)", output)
    detail = f"issuer: {issuer.group(1).strip()}" if issuer else "no certificate received"
    if done.returncode == 0 and code and code.group(1) == "0":
        return True, f"certificate and host name verified against {os.path.basename(ca_file)} ({detail})"
    error = re.search(r"verify error:num=\d+:([^\n]*)", output)
    reason = error.group(1).strip() if error else code.group(2) if code else "TLS handshake failed"
    return False, f"not verified: {reason} ({detail})"


def main() -> int:
    if len(sys.argv) != 2 or not os.environ.get("DATABASE_URL"):
        print("usage: DATABASE_URL=... check_db_cert.py <ca file>")
        return 2
    ok, message = check(os.environ["DATABASE_URL"], sys.argv[1])
    print(("OK: " if ok else "FAILED: ") + message)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
