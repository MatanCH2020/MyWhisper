"""Collector credentials stay transient; website counts installers across all pages."""
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("download_metrics", ROOT / "scripts/download_metrics.py")
metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics)


class Response:
    status = 200
    def __init__(self, body):
        self.body = json.dumps(body).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read(self, limit):
        return self.body[:limit]


class CollectorTest(unittest.TestCase):
    def environment(self):
        return {"GITHUB_REPOSITORY": "MatanCH2020/MyWhisper", "GITHUB_REF": "refs/heads/main",
                "GITHUB_EVENT_NAME": "workflow_dispatch",
                "ACTIONS_ID_TOKEN_REQUEST_URL": "https://example.actions.githubusercontent.com/token?x=1",
                "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "transient-job-secret"}

    def test_signed_trigger_and_persistence_acknowledgement(self):
        calls = []
        class Opener:
            def open(self, request, timeout):
                calls.append(request)
                return Response({"value": "signed-oidc-token-value"} if len(calls) == 1 else {"ok": True})
        self.assertEqual(metrics.collect("https://example.invalid/sync", self.environment(), Opener()), {"ok": True})
        self.assertIn("audience=mywhisper-download-sync", calls[0].full_url)
        self.assertEqual(calls[1].get_header("X-github-oidc-token"), "signed-oidc-token-value")

    def test_wrong_repository_branch_and_event_never_request_credentials(self):
        for key, value in (("GITHUB_REPOSITORY", "attacker/MyWhisper"),
                           ("GITHUB_REF", "refs/heads/other"), ("GITHUB_EVENT_NAME", "pull_request")):
            env = dict(self.environment(), **{key: value})
            with self.assertRaises(ValueError):
                metrics.collect("https://example.invalid/sync", env, object())

    def test_unconfirmed_snapshot_fails(self):
        class Opener:
            count = 0
            def open(self, request, timeout):
                self.count += 1
                return Response({"value": "signed-oidc-token-value"} if self.count == 1 else {"ok": False})
        with self.assertRaises(ValueError):
            metrics.collect("https://example.invalid/sync", self.environment(), Opener())


@unittest.skipUnless(shutil.which("node"), "Node is needed to exercise website JavaScript")
class WebsiteCountsTest(unittest.TestCase):
    def test_filters_assets_paginates_and_refuses_partial_totals(self):
        script = r'''
const assert = require('node:assert/strict');
const { summarize, load } = require('./docs/download-counts.js');
const win = {name:'MyWhisper-Setup.cmd',download_count:53};
const mac = {name:'MyWhisper-1.15.0b1-macos-arm64.dmg',download_count:19};
const release = {assets:[win,mac,{name:'SHA256SUMS.txt',download_count:800},{name:'macos-beta.md',download_count:8}]};
assert.deepEqual(summarize([release,{draft:true,assets:[win]}]),{windows:53,macos:19,total:72});
(async()=>{
 let calls=0;
 const total=await load(async(url)=>{calls++;return {ok:true,json:async()=>calls===1?Array.from({length:100},()=>({assets:[]})):[release]};});
 assert.equal(calls,2); assert.equal(total.total,72);
 calls=0;
 await assert.rejects(load(async()=>{calls++;return calls===1?{ok:true,json:async()=>Array.from({length:100},()=>release)}:{ok:false};}));
 assert.throws(()=>summarize([{assets:[{...win,download_count:-1}]}]));
})();
'''
        result = subprocess.run([shutil.which("node"), "-e", script], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
