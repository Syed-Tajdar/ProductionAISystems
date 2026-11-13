"""n8n's database connection with the server certificate checked (DEPLOY.md, "Check the database certificate").

    python3 -m unittest discover -s tests -p 'test_db_tls.py' -v

Starts Postgres 17 with TLS on, using a throwaway CA and server certificate made with openssl, and n8n 2.45 with
the settings DEPLOY.md gives for Render: DB_POSTGRESDB_SSL_REJECT_UNAUTHORIZED=true and DB_POSTGRESDB_SSL_CA_FILE.
n8n starts when the file holds the CA that signed the server certificate, and does not connect when it holds
another CA. The same server is used to test .github/scripts/check_db_cert.py, the check that runs before the
switch on Render. Needs Docker and openssl.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github" / "scripts" / "check_db_cert.py"
NETWORK, DB, N8N, PORT, DB_PORT = "n8n-leads-tls-test", "n8n-leads-tls-db", "n8n-leads-tls-n8n", 5683, 5684
N8N_IMAGE, PG_IMAGE = "n8nio/n8n:2.45.0", "postgres:17-alpine"
CERT_ERROR = ("self-signed certificate", "unable to verify the first certificate", "unable to get issuer")


def run(*cmd, check=True):
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def make_ca(folder: Path, name: str) -> Path:
    key, crt = folder / f"{name}.key", folder / f"{name}.crt"
    run("openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2", "-subj", f"/CN={name}",
        "-keyout", str(key), "-out", str(crt))
    return crt


def make_server_cert(folder: Path, ca: str, host: str) -> None:
    ext = folder / "san.cnf"
    ext.write_text(f"subjectAltName=DNS:{host},DNS:localhost\n")
    run("openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", f"/CN={host}",
        "-keyout", str(folder / "server.key"), "-out", str(folder / "server.csr"))
    run("openssl", "x509", "-req", "-in", str(folder / "server.csr"), "-CA", str(folder / f"{ca}.crt"),
        "-CAkey", str(folder / f"{ca}.key"), "-CAcreateserial", "-days", "2", "-extfile", str(ext),
        "-out", str(folder / "server.crt"))


@unittest.skipUnless(shutil.which("docker") and shutil.which("openssl"), "needs docker and openssl")
class DatabaseCertificate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="n8n-tls-"))
        cls.ca = make_ca(cls.tmp, "test-ca")
        cls.other_ca = make_ca(cls.tmp, "other-ca")
        make_server_cert(cls.tmp, "test-ca", DB)
        cls.cleanup()
        run("docker", "network", "create", NETWORK)
        # The key must belong to the postgres user and be private, so it is copied in before the server starts.
        start = ("cp /certs/server.crt /certs/server.key /var/lib/postgresql/ && chown postgres /var/lib/postgresql/server.* "
                 "&& chmod 600 /var/lib/postgresql/server.key && exec docker-entrypoint.sh postgres -c ssl=on "
                 "-c ssl_cert_file=/var/lib/postgresql/server.crt -c ssl_key_file=/var/lib/postgresql/server.key")
        run("docker", "create", "--name", DB, "--network", NETWORK, "-p", f"{DB_PORT}:5432",
            "-e", "POSTGRES_PASSWORD=test", "-e", "POSTGRES_DB=n8n",
            "--entrypoint", "sh", PG_IMAGE, "-c", start)
        run("docker", "cp", f"{cls.tmp}/.", f"{DB}:/certs")
        run("docker", "start", DB)
        for _ in range(60):
            if run("docker", "exec", DB, "pg_isready", "-U", "postgres", check=False).returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError("Postgres did not start: " + run("docker", "logs", "--tail", "20", DB, check=False).stderr)

    @classmethod
    def cleanup(cls):
        run("docker", "rm", "-f", N8N, DB, check=False)
        run("docker", "network", "rm", NETWORK, check=False)

    @classmethod
    def tearDownClass(cls):
        cls.cleanup()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def start_n8n(self, ca: Path, as_text: bool = False) -> None:
        """as_text: the certificate itself in DB_POSTGRESDB_SSL_CA, as pasted into a host's settings page."""
        run("docker", "rm", "-f", N8N, check=False)
        env = {"DB_TYPE": "postgresdb", "DB_POSTGRESDB_HOST": DB, "DB_POSTGRESDB_PORT": "5432", "DB_POSTGRESDB_DATABASE": "n8n",
               "DB_POSTGRESDB_USER": "postgres", "DB_POSTGRESDB_PASSWORD": "test", "DB_POSTGRESDB_SSL_ENABLED": "true",
               "DB_POSTGRESDB_SSL_REJECT_UNAUTHORIZED": "true", "DB_POSTGRESDB_SSL_CA_FILE": "/home/node/db-ca.crt",
               "N8N_ENCRYPTION_KEY": "tls-test-key-0123456789abcdef0123456789", "N8N_DIAGNOSTICS_ENABLED": "false",
               "N8N_VERSION_NOTIFICATIONS_ENABLED": "false", "N8N_LISTEN_ADDRESS": "0.0.0.0"}
        if as_text:
            del env["DB_POSTGRESDB_SSL_CA_FILE"]
            env["DB_POSTGRESDB_SSL_CA"] = ca.read_text()
        args = [x for k, v in env.items() for x in ("-e", f"{k}={v}")]
        run("docker", "create", "--name", N8N, "--network", NETWORK, "-p", f"{PORT}:5678", *args, N8N_IMAGE)
        run("docker", "cp", str(ca), f"{N8N}:/home/node/db-ca.crt")
        run("docker", "start", N8N)

    def logs(self) -> str:
        out = run("docker", "logs", N8N, check=False)
        return (out.stdout + out.stderr).lower()

    def healthy(self) -> bool:
        try:
            with urllib.request.urlopen(f"http://localhost:{PORT}/healthz", timeout=5) as r:
                return r.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def test_n8n_starts_when_the_ca_file_signed_the_server_certificate(self):
        self.start_n8n(self.ca)
        for _ in range(120):
            if self.healthy():
                break
            time.sleep(2)
        else:
            self.fail("n8n did not start: " + self.logs()[-1500:])
        used = run("docker", "exec", DB, "psql", "-U", "postgres", "-d", "n8n", "-Atc",
                   "select count(*) from pg_stat_ssl s join pg_stat_activity a using (pid) where s.ssl and a.datname = 'n8n' "
                   "and a.pid <> pg_backend_pid()").stdout.strip()
        self.assertGreater(int(used), 0)  # n8n's connections are encrypted
        self.assertFalse(any(word in self.logs() for word in CERT_ERROR))

    def test_n8n_starts_with_the_ca_given_as_text(self):
        self.start_n8n(self.ca, as_text=True)
        for _ in range(120):
            if self.healthy():
                break
            time.sleep(2)
        else:
            self.fail("n8n did not start: " + self.logs()[-1500:])
        self.assertFalse(any(word in self.logs() for word in CERT_ERROR))

    def test_n8n_does_not_connect_with_another_ca(self):
        self.start_n8n(self.other_ca)
        for _ in range(90):
            log = self.logs()
            if any(word in log for word in CERT_ERROR):
                break
            time.sleep(2)
        else:
            self.fail("no certificate error in the n8n log: " + self.logs()[-1500:])
        self.assertFalse(self.healthy())
        tables = run("docker", "exec", DB, "psql", "-U", "postgres", "-d", "n8n", "-Atc",
                     "select count(*) from pg_stat_activity where datname = 'n8n' and pid <> pg_backend_pid()").stdout.strip()
        self.assertEqual(tables, "0")  # no session was opened

    def check_script(self, ca: Path, host: str = "localhost"):
        env = {**os.environ, "DATABASE_URL": f"postgresql://postgres:s3cret-pass@{host}:{DB_PORT}/n8n"}
        done = subprocess.run([sys.executable, str(SCRIPT), str(ca)], capture_output=True, text=True, env=env)
        return done.returncode, done.stdout + done.stderr

    def test_check_script_accepts_the_right_ca_and_prints_no_connection_details(self):
        code, out = self.check_script(self.ca)
        self.assertEqual(code, 0, out)
        self.assertIn("OK: certificate and host name verified against test-ca.crt", out)
        self.assertIn("issuer: CN=test-ca", out.replace(" = ", "="))
        for secret in ("s3cret-pass", "localhost", str(DB_PORT)):
            self.assertNotIn(secret, out)

    def test_check_script_refuses_another_ca_and_a_wrong_host_name(self):
        code, out = self.check_script(self.other_ca)
        self.assertEqual(code, 1, out)
        self.assertIn("FAILED: not verified", out)
        code, out = self.check_script(self.ca, host="127.0.0.1")  # right CA, but the certificate is not for this name
        self.assertEqual(code, 1, out)
        self.assertRegex(out.lower(), "hostname mismatch|ip address mismatch")

    def test_the_committed_supabase_ca_is_a_ca_certificate(self):
        ca = ROOT / "n8n-leads" / "supabase" / "prod-ca-2021.crt"
        out = run("openssl", "x509", "-in", str(ca), "-noout", "-subject", "-fingerprint", "-sha256", "-ext", "basicConstraints").stdout
        self.assertIn("Supabase Root 2021 CA", out)
        self.assertIn("CA:TRUE", out)
        self.assertIn("80:70:25:AD:50:D4:ED:21:9D:2C:9C:7D:29:9C:00:4F:82:4E:B0:0C:F7:F6:5A:FE:F6:07:D0:7B:72:E6:CA:FA", out)


if __name__ == "__main__":
    unittest.main()
