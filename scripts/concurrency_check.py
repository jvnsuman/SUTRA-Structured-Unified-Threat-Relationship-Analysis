"""
scripts/concurrency_check.py

Fire N simultaneous GET /query/{case_id} requests at a RUNNING API and
report the status codes. Replaces the PowerShell/curl snippet so it works
in any shell (Windows PowerShell 5.1, PowerShell 7, cmd, bash).

Steps it performs:
  1. Log in (POST /auth/login) to get a bearer token.
  2. List your cases (GET /cases/).
  3. Fire N requests at once (a barrier lines the threads up) against the
     first case that actually has data. A case with no ingested entities
     answers 501, so those are skipped automatically.
  4. Print a tally of status codes. All 200 = pass; any 500 = fail.

Usage (API must already be running: uvicorn api.main:app --reload):
    python scripts/concurrency_check.py
    python scripts/concurrency_check.py --n 10 --case-id <uuid>
"""

import argparse
import collections
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx


def burst(client: httpx.Client, case_id: str, headers: dict, n: int) -> list[tuple[int, str]]:
    """Send n identical requests as simultaneously as possible."""
    barrier = threading.Barrier(n)

    def one_request(_: int) -> tuple[int, str]:
        barrier.wait()  # all threads release together
        r = client.get(f"/query/{case_id}", headers=headers)
        return r.status_code, r.text[:200]

    with ThreadPoolExecutor(max_workers=n) as pool:
        return list(pool.map(one_request, range(n)))


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--badge", default="INV001", help="seed_data.py demo investigator")
    p.add_argument("--password", default="investigator123")
    p.add_argument("--case-id", help="test this case only (default: first case with data)")
    p.add_argument("--n", type=int, default=5, help="number of simultaneous requests")
    args = p.parse_args()

    with httpx.Client(base_url=args.base_url, timeout=120) as client:
        # 1. Log in -> token
        login = client.post("/auth/login", json={"badge_id": args.badge, "password": args.password})
        if login.status_code != 200:
            print(f"Login failed ({login.status_code}): {login.text}")
            print("Is the API running, and has scripts/seed_data.py been run against its database?")
            return 2
        headers = {"Authorization": f"Bearer {login.json()['token']}"}
        print(f"Logged in as {args.badge}.")

        # 2. Which cases to try
        if args.case_id:
            case_ids = [args.case_id]
        else:
            r = client.get("/cases/", headers=headers)
            case_ids = [c["id"] for c in r.json()["cases"]]
            print(f"{len(case_ids)} case(s) visible to this user.")
        if not case_ids:
            print("No cases found. Run scripts/populate_demo_case.py first.")
            return 2

        # 3. Burst against the first case that isn't 501 (no data yet)
        for case_id in case_ids:
            start = time.perf_counter()
            results = burst(client, case_id, headers, args.n)
            elapsed = time.perf_counter() - start
            tally = collections.Counter(code for code, _ in results)

            if set(tally) == {501}:
                print(f"  {case_id}: 501 (no ingested data), trying next case...")
                continue

            print(f"\nCase {case_id}: {args.n} simultaneous requests in {elapsed:.2f}s")
            for code, count in sorted(tally.items()):
                print(f"  HTTP {code}: {count}")
            bad = [(c, body) for c, body in results if c != 200]
            for code, body in bad[:3]:
                print(f"  sample non-200 -> {code}: {body}")
            print("\nPASS" if not bad else "\nFAIL")
            return 0 if not bad else 1

        print("Every case returned 501 (no ingested data). Run scripts/populate_demo_case.py, then retry.")
        return 2


if __name__ == "__main__":
    sys.exit(main())
