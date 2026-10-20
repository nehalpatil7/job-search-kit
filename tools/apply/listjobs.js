#!/usr/bin/env node
/*
 * Render a custom careers page and list its job links (used by tools/careers.py for non-ATS sites).
 *   node tools/apply/listjobs.js <careers url>
 * Prints JSON: {"embedded": "<ATS board url or null>", "jobs": [{"title", "url", "location"}]}
 */
const puppeteer = require('puppeteer');

const ATS = /(?:boards|job-boards)\.greenhouse\.io\/[\w-]+|jobs\.lever\.co\/[\w.-]+|jobs\.ashbyhq\.com\/[\w.%-]+|[\w-]+\.wd\d+\.myworkdayjobs\.com\/(?:[a-z]{2}-[A-Z]{2}\/)?[\w-]+|(?:careers|jobs)\.smartrecruiters\.com\/[\w-]+/;

(async () => {
  const url = process.argv[2];
  const browser = await puppeteer.launch({
    headless: true,
    executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || undefined,
    args: process.getuid && process.getuid() === 0 ? ['--no-sandbox'] : [],
  });
  try {
    const page = await browser.newPage();
    await page.setUserAgent('Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129 Safari/537.36');
    await page.goto(url, { waitUntil: 'networkidle2', timeout: 60000 });
    // load lazy lists and "show more" pages a little
    for (let i = 0; i < 6; i++) {
      await page.evaluate(() => window.scrollTo(0, document.body.scrollHeight));
      await new Promise((r) => setTimeout(r, 700));
    }
    const frames = page.frames().map((f) => f.url()).join(' ');
    const html = await page.content();
    const m = (frames + ' ' + html).match(ATS);
    const jobs = await page.evaluate(() => {
      const out = [], seen = new Set();
      const jobish = /\/(jobs?|careers?|positions?|openings?|roles?|vacanc\w*|requisitions?)\/|gh_jid=|jobId=|job_id=|\/j\/|\/o\//i;
      for (const a of document.querySelectorAll('a[href]')) {
        const href = a.href, text = (a.innerText || a.textContent || '').replace(/\s+/g, ' ').trim();
        if (!jobish.test(href) || !text || text.length < 4 || text.length > 160 || seen.has(href)) continue;
        if (/^(view|see|all|apply|learn more|jobs|careers|search)\b/i.test(text) && text.split(' ').length < 4) continue;
        seen.add(href);
        const card = a.closest('li, tr, article, [class*="job"], [class*="position"], [class*="opening"]') || a.parentElement;
        const loc = card ? (card.innerText || '').replace(text, '').replace(/\s+/g, ' ').trim().slice(0, 120) : '';
        out.push({ title: text.split('\n')[0].slice(0, 140), url: href, location: loc });
      }
      return out;
    });
    console.log(JSON.stringify({ embedded: m ? 'https://' + m[0] : null, jobs }));
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e.message); process.exit(1); });
