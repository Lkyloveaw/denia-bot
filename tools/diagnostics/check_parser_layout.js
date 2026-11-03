// Run inside the renderer image with NODE_PATH=/app/node_modules.
// Input: a directory containing parser HTML fixtures and manifest.json.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');

(async () => {
  const dir = process.argv[2];
  const fixtures = JSON.parse(fs.readFileSync(path.join(dir, 'manifest.json'), 'utf8'));
  const browser = await chromium.launch({headless: true, args: ['--no-sandbox']});
  const failures = [];
  try {
    for (const fixture of fixtures) {
      const page = await browser.newPage({viewport: {width: 800, height: 2400}, javaScriptEnabled: false});
      try {
        await page.setContent(fs.readFileSync(path.join(dir, fixture.file), 'utf8'));
        await page.evaluate(async () => {
          await document.fonts.ready;
          await Promise.all([...document.images].map(image => image.decode()));
        });
        const headers = await page.evaluate(() => [...document.querySelectorAll('header')].map(header => {
          const rect = element => element ? element.getBoundingClientRect().toJSON() : null;
          return {header: rect(header), platform: rect(header.querySelector('.platform')),
            logo: rect(header.querySelector('.logo')), author: rect(header.querySelector('.author')),
            platformText: header.querySelector('.platform')?.textContent.trim(),
            logoSource: header.querySelector('.logo')?.getAttribute('src'),
            overflows: [...header.querySelectorAll('*')].filter(el => el.scrollWidth > el.clientWidth + 1)
              .map(el => el.className)};
        }));
        assert.equal(headers.length, fixture.headers, 'all nested headers must remain visible');
        for (const {header, platform, logo, author, overflows, platformText, logoSource} of headers) {
          assert.ok(platform, 'platform label is missing');
          assert.ok(Math.abs(platform.right - header.right) <= 1,
            `badge must align right: badge.right=${platform.right}, header.right=${header.right}`);
          assert.ok(Math.abs(platform.top - header.top) <= 1, 'badge must stay at the top');
          if (fixture.logo) {
            assert.ok(logo, 'platform logo is missing');
            assert.equal(platformText, '', 'do not duplicate the logo with a visible platform name');
            assert.ok(logo.height >= 39 && logo.width > 28, 'logo must be enlarged proportionally');
            assert.ok(logoSource.startsWith('data:image/png;base64,'), 'preserve the original PNG transparency');
            assert.ok(Math.abs(logo.right - header.right) <= 1,
              `logo must be at the right edge: logo.right=${logo.right}, header.right=${header.right}`);
          }
          if (author) {
            assert.ok(author.right + 8 <= platform.left, 'author and badge overlap');
            assert.ok(Math.abs(author.left - header.left) <= 1, 'author must remain on the left');
          }
          assert.deepEqual(overflows, [], 'header text must wrap inside the available width');
        }
        console.log(`PASS ${fixture.name} (${headers.length} headers)`);
      } catch (error) {
        failures.push(fixture.name);
        console.error(`FAIL ${fixture.name}: ${error.message}`);
      } finally { await page.close(); }
    }
  } finally { await browser.close(); }
  console.log(JSON.stringify({total: fixtures.length, passed: fixtures.length - failures.length, failures}));
  if (failures.length) process.exitCode = 1;
})().catch(error => { console.error(error); process.exitCode = 1; });
