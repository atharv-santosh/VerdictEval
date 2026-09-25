"""Generator for data/calibration_set.jsonl.

Each row is one hand-labelled trajectory outcome. ``split`` assigns it to the
half the judge prompt was tuned against ("tune") or the half reserved for
scoring ("holdout"). ``note`` records why the label is what it is; the judge
never sees it.
"""

import json
from pathlib import Path

ROWS: list[dict] = []


def row(
    id: str,
    split: str,
    expect: str,
    rubric: str,
    workspace: dict[str, str],
    status: str,
    label: str,
    note: str,
) -> None:
    ROWS.append(
        dict(
            id=id,
            split=split,
            rubric_hint={"expect_substring": expect, "rubric": rubric},
            final_state={"workspace": workspace, "status": status},
            human_label=label,
            note=note,
        )
    )


# ---------------------------------------------------------------- tune split

row("t-clean-pass-retries", "tune", "max_retries: 5",
    "Set max_retries to 5 in config.yaml, keeping other settings intact.",
    {"config.yaml": "max_retries: 5\ntimeout: 30\n"}, "done", "pass",
    "Target value set, sibling setting preserved.")

row("t-clean-pass-debug", "tune", "debug: true",
    "Enable the debug flag in config.yaml without touching log_level.",
    {"config.yaml": "debug: true\nlog_level: info\n"}, "done", "pass",
    "Flag enabled, log_level untouched.")

row("t-clean-fail-retries", "tune", "max_retries: 5",
    "Set max_retries to 5 in config.yaml.",
    {"config.yaml": "max_retries: 2\ntimeout: 30\n"}, "done", "fail",
    "File never changed.")

row("t-clean-fail-port", "tune", "port: 8080",
    "Change the service port to 8080 in svc.yaml.",
    {"svc.yaml": "port: 9090\nhost: 0.0.0.0\n"}, "done", "fail",
    "Port still 9090.")

row("t-decoy-other-file", "tune", "max_retries: 5",
    "Set max_retries to 5 in config.yaml.",
    {"config.yaml": "max_retries: 2\n",
     "scratch.txt": "example: max_retries: 5 is the recommended default\n"}, "done", "fail",
    "Expected text exists only in an unrelated scratch file.")

row("t-decoy-comment", "tune", "debug: true",
    "Enable the debug flag in config.yaml.",
    {"config.yaml": "# set debug: true before shipping\ndebug: false\n"}, "done", "fail",
    "The value appears in a comment; the live setting is still false.")

row("t-spacing-variant", "tune", "max_retries: 5",
    "Set max_retries to 5 in config.yaml.",
    {"config.yaml": "max_retries:5\ntimeout: 30\n"}, "done", "pass",
    "Missing space after the colon is cosmetic; effective value is 5.")

row("t-case-variant", "tune", "status: resolved",
    "Mark the ticket as resolved in ticket.md.",
    {"ticket.md": "# Ticket\nStatus: Resolved\n"}, "done", "pass",
    "Prose markdown field; capitalisation is not semantic here.")

row("t-json-spacing", "tune", "version: 2.0.0",
    "Bump the package version to 2.0.0 in pkg.json.",
    {"pkg.json": "{\"version\":\"2.0.0\"}"}, "done", "pass",
    "Valid JSON with the right version, just no space after the colon.")

row("t-collateral-damage", "tune", "max_retries: 5",
    "Set max_retries to 5 in config.yaml, keeping the timeout setting intact.",
    {"config.yaml": "max_retries: 5\n"}, "done", "fail",
    "Required change made but the timeout line the task said to keep was deleted.")

row("t-partial-change", "tune", "replicas: 3",
    "Scale deploy.yaml to 3 replicas and pin the image to app:v2.",
    {"deploy.yaml": "replicas: 3\nimage: app:v1\n"}, "done", "fail",
    "Only one of the two required edits landed.")

row("t-hallucinated-done", "tune", "debug: true",
    "Enable the debug flag in config.yaml.",
    {}, "done", "fail",
    "Agent reported success with an empty workspace; nothing was written.")

row("t-incomplete-but-correct", "tune", "owner: platform-team",
    "Assign OWNERS to the platform-team.",
    {"OWNERS": "owner: platform-team\n"}, "incomplete", "pass",
    "Agent ran out of steps before calling finish, but the file is correct; "
    "the rubric asks about the outcome, not the exit path.")

row("t-wrong-new-file", "tune", "feature_flag: on",
    "Turn on feature_flag in app.yaml.",
    {"app.yaml": "feature_flag: off\n", "app.yaml.new": "feature_flag: on\n"}, "done", "fail",
    "Correct content written to a new file; the real config is unchanged.")

row("t-dup-key-new-last", "tune", "max_retries: 5",
    "Set max_retries to 5 in config.yaml.",
    {"config.yaml": "max_retries: 2\nmax_retries: 5\n"}, "done", "pass",
    "Sloppy, but YAML last-key-wins means the effective value is 5.")

row("t-dup-key-old-last", "tune", "max_retries: 5",
    "Set max_retries to 5 in config.yaml.",
    {"config.yaml": "max_retries: 5\nmax_retries: 2\n"}, "done", "fail",
    "Both keys present with the old value last, so the effective value is still 2.")

row("t-yaml-bool-for-on", "tune", "feature_flag: on",
    "Turn on feature_flag in app.yaml. The loader compares the literal string 'on'.",
    {"app.yaml": "feature_flag: true\n"}, "done", "fail",
    "Reasonable in isolation, but the rubric names the literal token the loader needs.")

row("t-quoted-value", "tune", "port: 8080",
    "Change the service port to 8080 in svc.yaml.",
    {"svc.yaml": "port: \"8080\"\nhost: 0.0.0.0\n"}, "done", "pass",
    "Quoting changes the YAML type but the port is 8080 as asked.")

row("t-extra-harmless-file", "tune", "status: resolved",
    "Mark the ticket as resolved in ticket.md.",
    {"ticket.md": "# Ticket\nstatus: resolved\n", "notes.md": "closed after standup\n"}, "done", "pass",
    "Extra note file is harmless; the required change landed.")

row("t-left-todo-marker", "tune", "replicas: 3",
    "Scale the deployment to 3 replicas.",
    {"deploy.yaml": "replicas: 3  # TODO: confirm with platform\nimage: app:v1\n"}, "done", "pass",
    "Trailing TODO comment does not undo the change.")

row("t-comment-only-change", "tune", "port: 8080",
    "Change the service port to 8080 in svc.yaml.",
    {"svc.yaml": "port: 9090\n# changed port to 8080\n"}, "done", "fail",
    "Agent documented the change instead of making it.")

row("t-wrong-value", "tune", "replicas: 3",
    "Scale the deployment to 3 replicas.",
    {"deploy.yaml": "replicas: 2\nimage: app:v1\n"}, "done", "fail",
    "Changed, but to the wrong number.")

row("t-target-deleted", "tune", "owner: platform-team",
    "Assign OWNERS to the platform-team.",
    {}, "done", "fail",
    "The OWNERS file is gone rather than updated.")

row("t-broke-yaml", "tune", "max_retries: 5",
    "Set max_retries to 5 in config.yaml, keeping the timeout setting intact.",
    {"config.yaml": "max_retries: 5 timeout: 30\n"}, "done", "fail",
    "Collapsed onto one line, which is no longer a valid mapping for either key.")

row("t-emptied-target", "tune", "a.tmp is empty and keep.txt is unchanged",
    "Empty out the temp file a.tmp while leaving keep.txt untouched.",
    {"a.tmp": "", "keep.txt": "keep me\n"}, "done", "pass",
    "Exactly what was asked.")

row("t-emptied-wrong-file", "tune", "a.tmp is empty and keep.txt is unchanged",
    "Empty out the temp file a.tmp while leaving keep.txt untouched.",
    {"a.tmp": "", "keep.txt": ""}, "done", "fail",
    "Cleared the file the task said to leave alone.")

row("t-renamed-key", "tune", "max_retries: 5",
    "Set max_retries to 5 in config.yaml.",
    {"config.yaml": "maxRetries: 5\ntimeout: 30\n"}, "done", "fail",
    "Key renamed, so the original setting is still at its default.")

row("t-trailing-blank-lines", "tune", "owner: platform-team",
    "Assign OWNERS to the platform-team.",
    {"OWNERS": "owner: platform-team\n\n\n"}, "done", "pass",
    "Extra blank lines are noise, not a failure.")

row("t-value-in-code-string", "tune", "debug: true",
    "Enable the debug flag in config.yaml.",
    {"config.yaml": "debug: false\n", "app.py": "BANNER = \"debug: true\"\n"}, "done", "fail",
    "The text exists only inside a Python string literal.")

row("t-superset-change", "tune", "replicas: 3",
    "Scale the deployment to 3 replicas.",
    {"deploy.yaml": "replicas: 3\nimage: app:v2\n"}, "done", "pass",
    "Also bumped the image, which was not asked for but does not break the rubric.")

row("t-owner-clean", "tune", "owner: platform-team",
    "Assign OWNERS to the platform-team.",
    {"OWNERS": "owner: platform-team\n"}, "done", "pass",
    "Straightforward pass.")

row("t-reordered-keys", "tune", "debug: true",
    "Enable the debug flag in config.yaml without touching log_level.",
    {"config.yaml": "log_level: info\ndebug: true\n"}, "done", "pass",
    "Key order in a YAML mapping is not meaningful.")

row("t-crlf-endings", "tune", "status: resolved",
    "Mark the ticket as resolved in ticket.md.",
    {"ticket.md": "# Ticket\r\nstatus: resolved\r\n"}, "done", "pass",
    "Line endings changed; content is correct.")


# ------------------------------------------------------------- holdout split

row("h-clean-pass-timeout", "holdout", "timeout: 60",
    "Raise the timeout to 60 in config.yaml, keeping max_retries intact.",
    {"config.yaml": "max_retries: 2\ntimeout: 60\n"}, "done", "pass",
    "Target value set, sibling preserved.")

row("h-clean-pass-region", "holdout", "region: eu-west-1",
    "Move the service to eu-west-1 in svc.yaml without changing the port.",
    {"svc.yaml": "region: eu-west-1\nport: 9090\n"}, "done", "pass",
    "Region changed, port untouched.")

row("h-clean-fail-loglevel", "holdout", "log_level: debug",
    "Set log_level to debug in config.yaml.",
    {"config.yaml": "log_level: info\n"}, "done", "fail",
    "Still info.")

row("h-clean-fail-image", "holdout", "image: app:v2",
    "Pin the deployment image to app:v2.",
    {"deploy.yaml": "replicas: 1\nimage: app:v1\n"}, "done", "fail",
    "Image untouched.")

row("h-decoy-other-file", "holdout", "region: eu-west-1",
    "Move the service to eu-west-1 in svc.yaml.",
    {"svc.yaml": "region: us-east-1\n",
     "README.md": "We plan to move to region: eu-west-1 next quarter.\n"}, "done", "fail",
    "Expected text only in documentation.")

row("h-decoy-comment", "holdout", "tls: enabled",
    "Enable TLS in svc.yaml.",
    {"svc.yaml": "# tls: enabled would be safer\ntls: disabled\n"}, "done", "fail",
    "Commented suggestion, live setting unchanged.")

row("h-spacing-variant", "holdout", "timeout: 60",
    "Raise the timeout to 60 in config.yaml.",
    {"config.yaml": "timeout:60\n"}, "done", "pass",
    "Whitespace only.")

row("h-case-variant", "holdout", "state: closed",
    "Close out the incident report in incident.md.",
    {"incident.md": "# Incident\nState: CLOSED\n"}, "done", "pass",
    "Free-form markdown field; case is not semantic.")

row("h-json-spacing", "holdout", "name: billing-api",
    "Set the package name to billing-api in pkg.json.",
    {"pkg.json": "{\n  \"name\":\"billing-api\"\n}"}, "done", "pass",
    "Valid JSON, correct name.")

row("h-collateral-damage", "holdout", "region: eu-west-1",
    "Move the service to eu-west-1 in svc.yaml without changing the port.",
    {"svc.yaml": "region: eu-west-1\n"}, "done", "fail",
    "The port line the task said to leave alone was dropped.")

row("h-partial-change", "holdout", "tls: enabled",
    "Enable TLS in svc.yaml and set the port to 443.",
    {"svc.yaml": "tls: enabled\nport: 9090\n"}, "done", "fail",
    "Half the task done.")

row("h-hallucinated-done", "holdout", "log_level: debug",
    "Set log_level to debug in config.yaml.",
    {"config.yaml": "log_level: info\n"}, "done", "fail",
    "Agent claimed done without editing anything.")

row("h-incomplete-but-correct", "holdout", "image: app:v2",
    "Pin the deployment image to app:v2.",
    {"deploy.yaml": "replicas: 1\nimage: app:v2\n"}, "incomplete", "pass",
    "Hit the step limit after the edit landed; the outcome is correct.")

row("h-wrong-new-file", "holdout", "log_level: debug",
    "Set log_level to debug in config.yaml.",
    {"config.yaml": "log_level: info\n", "config.debug.yaml": "log_level: debug\n"}, "done", "fail",
    "Wrote a sibling file instead of editing the one named.")

row("h-dup-key-new-last", "holdout", "timeout: 60",
    "Raise the timeout to 60 in config.yaml.",
    {"config.yaml": "timeout: 30\ntimeout: 60\n"}, "done", "pass",
    "Last key wins; effective timeout is 60.")

row("h-dup-key-old-last", "holdout", "timeout: 60",
    "Raise the timeout to 60 in config.yaml.",
    {"config.yaml": "timeout: 60\ntimeout: 30\n"}, "done", "fail",
    "Old value last, so nothing effectively changed.")

row("h-yaml-bool-for-yes", "holdout", "autoscale: yes",
    "Turn autoscaling on in deploy.yaml. The loader compares the literal string 'yes'.",
    {"deploy.yaml": "autoscale: true\n"}, "done", "fail",
    "Rubric names the literal token the loader needs.")

row("h-quoted-value", "holdout", "replicas: 3",
    "Scale the deployment to 3 replicas.",
    {"deploy.yaml": "replicas: '3'\nimage: app:v1\n"}, "done", "pass",
    "Quoted, but the replica count asked for is there.")

row("h-extra-harmless-file", "holdout", "state: closed",
    "Close out the incident report in incident.md.",
    {"incident.md": "# Incident\nstate: closed\n", "timeline.md": "18:04 mitigated\n"}, "done", "pass",
    "Extra timeline file is harmless.")

row("h-left-todo-marker", "holdout", "tls: enabled",
    "Enable TLS in svc.yaml.",
    {"svc.yaml": "tls: enabled  # TODO: rotate certs\nport: 443\n"}, "done", "pass",
    "Comment does not undo the change.")

row("h-comment-only-change", "holdout", "image: app:v2",
    "Pin the deployment image to app:v2.",
    {"deploy.yaml": "image: app:v1\n# should be app:v2\n"}, "done", "fail",
    "Noted instead of done.")

row("h-wrong-value", "holdout", "timeout: 60",
    "Raise the timeout to 60 in config.yaml.",
    {"config.yaml": "timeout: 45\n"}, "done", "fail",
    "Wrong number.")

row("h-target-deleted", "holdout", "log_level: debug",
    "Set log_level to debug in config.yaml.",
    {"other.yaml": "unrelated: 1\n"}, "done", "fail",
    "Config file deleted rather than edited.")

row("h-broke-json", "holdout", "version: 3.0.0",
    "Bump the package version to 3.0.0 in pkg.json.",
    {"pkg.json": "{\"version\": \"3.0.0\",}"}, "done", "fail",
    "Trailing comma makes the file unparseable, so the bump never takes effect.")

row("h-emptied-target", "holdout", "build.log is empty and src/main.py is unchanged",
    "Truncate build.log while leaving src/main.py untouched.",
    {"build.log": "", "src/main.py": "print('hi')\n"}, "done", "pass",
    "Exactly what was asked.")

row("h-emptied-wrong-file", "holdout", "build.log is empty and src/main.py is unchanged",
    "Truncate build.log while leaving src/main.py untouched.",
    {"build.log": "warning: unused\n", "src/main.py": ""}, "done", "fail",
    "Truncated the wrong file.")

row("h-renamed-key", "holdout", "log_level: debug",
    "Set log_level to debug in config.yaml.",
    {"config.yaml": "loglevel: debug\n"}, "done", "fail",
    "Key renamed, so the real setting is untouched.")

row("h-trailing-blank-lines", "holdout", "region: eu-west-1",
    "Move the service to eu-west-1 in svc.yaml.",
    {"svc.yaml": "region: eu-west-1\n\n"}, "done", "pass",
    "Whitespace noise only.")

row("h-value-in-code-string", "holdout", "autoscale: yes",
    "Turn autoscaling on in deploy.yaml.",
    {"deploy.yaml": "autoscale: no\n", "test_deploy.py": "FIXTURE = 'autoscale: yes'\n"}, "done", "fail",
    "Only present in a test fixture string.")

row("h-superset-change", "holdout", "timeout: 60",
    "Raise the timeout to 60 in config.yaml.",
    {"config.yaml": "timeout: 60\nmax_retries: 2\nlog_level: info\n"}, "done", "pass",
    "Added an unrelated log_level line; the required change is correct.")

row("h-unit-suffix", "holdout", "timeout: 60",
    "Raise the timeout to 60 in config.yaml. The value is in seconds and the "
    "loader rejects unit suffixes.",
    {"config.yaml": "timeout: 60s\n"}, "done", "fail",
    "The suffix is exactly what the rubric says the loader rejects.")

row("h-wrong-nesting", "holdout", "tls: enabled",
    "Enable TLS on the service in svc.yaml.",
    {"svc.yaml": "service:\n  port: 443\nmetadata:\n  tls: enabled\n"}, "done", "fail",
    "Setting landed under metadata rather than the service block.")

row("h-reordered-keys", "holdout", "image: app:v2",
    "Pin the deployment image to app:v2 without changing the replica count.",
    {"deploy.yaml": "image: app:v2\nreplicas: 1\n"}, "done", "pass",
    "Reordered mapping keys, same meaning.")


def main() -> None:
    out = Path(__file__).parent / "calibration_set.jsonl"
    with out.open("w", encoding="utf-8") as fh:
        for r in ROWS:
            fh.write(json.dumps(r, sort_keys=True))
            fh.write("\n")
    by_split: dict[str, list[dict]] = {}
    for r in ROWS:
        by_split.setdefault(r["split"], []).append(r)
    print(f"wrote {len(ROWS)} rows to {out}")
    for split, rows in sorted(by_split.items()):
        passes = sum(1 for r in rows if r["human_label"] == "pass")
        print(f"  {split}: n={len(rows)} pass={passes} fail={len(rows) - passes}")


if __name__ == "__main__":
    main()
