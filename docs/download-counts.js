/* Counts downloadable installers, never users, installations or ancillary files. */
(function (root) {
  'use strict';
  function summarize(releases) {
    const totals = { windows: 0, macos: 0 };
    for (const release of releases) {
      if (release.draft) continue;
      for (const asset of release.assets || []) {
        const count = asset.download_count;
        if (!Number.isSafeInteger(count) || count < 0) throw new Error('Invalid download count');
        if (asset.name === 'MyWhisper-Setup.cmd') totals.windows += count;
        else if (/^MyWhisper-\d+\.\d+\.\d+(?:b\d+)?-macos-arm64\.dmg$/.test(asset.name)) totals.macos += count;
      }
    }
    return { ...totals, total: totals.windows + totals.macos };
  }
  async function load(fetcher = fetch) {
    const releases = [];
    for (let page = 1; page <= 100; page++) {
      const response = await fetcher('https://api.github.com/repos/MatanCH2020/MyWhisper/releases?per_page=100&page=' + page);
      if (!response.ok) throw new Error('GitHub unavailable');
      const data = await response.json();
      if (!Array.isArray(data)) throw new Error('Invalid releases');
      releases.push(...data);
      if (data.length < 100) return summarize(releases);
    }
    throw new Error('Incomplete release pagination');
  }
  const api = { summarize, load };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.MyWhisperDownloads = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
