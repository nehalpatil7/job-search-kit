#!/usr/bin/env node
/*
 * Prefill a job application in a visible Chrome window, using one tracker record.
 *
 *   node tools/apply/autofill.js <record id> [--url <application url>]
 *
 * - Opens the job's link (or --url). Click "Apply" yourself if the form is on another page.
 * - Attaches that job's resume PDF to the resume/CV upload field.
 * - Asks Gemini to map form fields to base/profile.json, the job's saved application answers,
 *   and its resume text. Fields with no supporting data are left empty and listed as "NEEDS YOU".
 * - Never fills demographic / EEO / self-identification questions, and never clicks Submit or Next.
 * - Keeps watching: when a new page or new fields appear (multi-step forms), it fills those too.
 * - If the page shows an "application submitted" confirmation, the job is marked Applied.
 * - Learns from you: when you fill a field it left empty (or correct one it filled), your answer is saved.
 *   General answers ("How did you hear about us?", "Notice period?") go to base/profile.json "learned";
 *   job-specific open-ended answers ("Why do you want to work at X?") go to that job's saved answers.
 *   Both are reused automatically next time. Demographic, consent and legal-attestation answers are never saved.
 * - Ends when you close the browser window.
 *
 * Needs: GEMINI_API_KEY in the environment (never hardcode it). Optional: GEMINI_MODEL.
 */
const fs = require('fs');
const path = require('path');
const { execFileSync } = require('child_process');

const ROOT = path.resolve(__dirname, '..', '..');
const MODEL = process.env.GEMINI_MODEL || 'gemini-3-flash-preview';
const KEY = process.env.GEMINI_API_KEY;
const FAKE_LLM = process.env.AUTOFILL_FAKE_LLM === '1';   // tests only: keyword mapping, no API call
const HEADLESS = process.env.AUTOFILL_HEADLESS === '1';   // tests only
const PROFILE_DIR = path.join(__dirname, '.chrome-profile'); // keeps your ATS logins between runs
const CONFIRMED = /thank you for (your )?appl|application (has been |was )?(submitted|received)|we('ve| have) received your application/i;
const SENSITIVE = /gender|sex\b|race|ethnic|hispanic|latino|veteran|disabilit|sexual orientation|transgender|pronoun|self[- ]identif|date of birth|\bage\b|religio|marital|ssn|social security/i;

const NO_LEARN = /agree|consent|certify|attest|acknowledge|terms|privacy|signature|sign here|i understand|password|captcha/i;
const PROFILE_PATH = path.join(ROOT, 'base', 'profile.json');

const log = (...a) => console.log(...a);
const norm = (q) => (q || '').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();

function arg(name) {
  const i = process.argv.indexOf(name);
  return i > -1 ? process.argv[i + 1] : null;
}

function tracker(...args) {
  return execFileSync('python3', [path.join(ROOT, 'tools', 'tracker.py'), ...args], { cwd: ROOT, encoding: 'utf8' });
}

// ---------- page scanning (runs inside each frame) ----------

function scanFrame() {
  const visible = (el) => {
    if (el.type === 'file') return true; // upload inputs are often hidden behind a styled button
    const r = el.getBoundingClientRect(), s = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const text = (n) => (n ? (n.innerText || n.textContent || '').replace(/\s+/g, ' ').trim() : '');
  const labelOf = (el) => {
    let t = '';
    if (el.id) t = text(document.querySelector(`label[for="${CSS.escape(el.id)}"]`));
    if (!t && el.closest('label')) t = text(el.closest('label'));
    if (!t && el.getAttribute('aria-labelledby'))
      t = el.getAttribute('aria-labelledby').split(/\s+/).map((id) => text(document.getElementById(id))).join(' ');
    if (!t) t = el.getAttribute('aria-label') || '';
    return t.slice(0, 200);
  };
  const questionOf = (el) => {
    const fs = el.closest('fieldset');
    if (fs && fs.querySelector('legend')) return text(fs.querySelector('legend')).slice(0, 300);
    // nearest preceding block of text, for widget-style forms
    let n = el.closest('div, li, section');
    for (let i = 0; n && i < 4; i++, n = n.parentElement) {
      const lbl = n.querySelector('label, legend, [class*="label"], [class*="question"], h3, h4, p');
      if (lbl && text(lbl) && !lbl.contains(el)) return text(lbl).slice(0, 300);
    }
    return '';
  };
  window.__afNext = window.__afNext || 1;
  const out = [];
  for (const el of document.querySelectorAll('input, textarea, select')) {
    const type = (el.getAttribute('type') || el.tagName).toLowerCase();
    if (['hidden', 'submit', 'button', 'reset', 'image', 'password', 'search'].includes(type)) continue;
    if (el.disabled || el.readOnly || !visible(el)) continue;
    if (!el.dataset.afId) el.dataset.afId = String(window.__afNext++);
    const f = {
      id: el.dataset.afId, tag: el.tagName.toLowerCase(), type,
      name: el.name || '', label: labelOf(el), question: questionOf(el),
      placeholder: el.placeholder || '', required: el.required || el.getAttribute('aria-required') === 'true',
      role: el.getAttribute('role') || '',
      filled: type === 'checkbox' || type === 'radio' ? el.checked : type === 'file' ? el.files.length > 0 : !!el.value,
    };
    if (f.tag === 'select') f.options = Array.from(el.options).map((o) => ({ value: o.value, text: o.text.trim() })).slice(0, 80);
    if (type === 'radio' || type === 'checkbox') f.option = f.label;
    out.push(f);
  }
  return { fields: out, text: (document.body ? document.body.innerText : '').slice(0, 4000) };
}

async function scanAll(page) {
  const all = [];
  let pageText = '';
  for (const [fi, frame] of page.frames().entries()) {
    try {
      const { fields, text } = await frame.evaluate(scanFrame);
      fields.forEach((f) => all.push({ ...f, key: `${fi}:${f.id}`, frame }));
      pageText += '\n' + text;
    } catch (_) { /* cross-origin or detached frame */ }
  }
  return { fields: all, pageText };
}

// ---------- mapping fields to answers ----------

const SCHEMA = {
  type: 'OBJECT',
  properties: {
    actions: {
      type: 'ARRAY',
      items: {
        type: 'OBJECT',
        properties: {
          key: { type: 'STRING', description: 'The field key exactly as given' },
          action: { type: 'STRING', description: "'type' for text, textarea and comboboxes; 'select' for <select>; 'check' for a radio/checkbox to tick" },
          value: { type: 'STRING', description: "Text to type, the <option> value to select, or 'true' for check" },
        },
        required: ['key', 'action', 'value'],
      },
    },
    needs_you: {
      type: 'ARRAY',
      items: {
        type: 'OBJECT',
        properties: { key: { type: 'STRING' }, question: { type: 'STRING' }, reason: { type: 'STRING' } },
        required: ['question', 'reason'],
      },
    },
  },
  required: ['actions', 'needs_you'],
};

function buildPrompt(fields, ctx) {
  const slim = fields.map(({ frame, filled, id, ...f }) => f);
  return `You fill in a job application form for the candidate using ONLY the data below.

Rules:
- Use only facts present in PROFILE, SAVED ANSWERS, RESUME TEXT, or JOB. Never guess or invent anything.
- Open-ended questions (textareas, "why us", "describe a project"): use a SAVED ANSWER only if it answers the same question. Otherwise put the field in needs_you with reason "no saved answer".
- Demographic, EEO, voluntary self-identification, disability, veteran, gender, race, or similar: never fill. Add to needs_you with reason "personal choice".
- LEARNED ANSWERS are the candidate's own answers from earlier forms, and are newer than PROFILE: if they disagree, LEARNED wins. Reuse one when the field asks the same thing (wording can differ); for <select>/radio pick the option that matches it.
- Legal attestations, consent checkboxes, terms and signatures: always needs_you. Salary expectations, start date, and sponsorship/authorization: only from PROFILE or LEARNED ANSWERS, otherwise needs_you.
- For <select>, value must be one of that field's option values. For radio/checkbox fields, return action 'check' only for the one option that is right.
- Text comboboxes (role=combobox): action 'type' with the exact option text you expect.
- Skip file inputs; they are handled separately. Skip fields that are already filled.
- Prefer leaving a field empty over a doubtful answer.

JOB: ${JSON.stringify(ctx.job)}
PROFILE: ${JSON.stringify(ctx.profileCore)}
LEARNED ANSWERS: ${JSON.stringify(ctx.learned)}
SAVED ANSWERS: ${JSON.stringify(ctx.qa)}
RESUME TEXT:
${ctx.resumeText}

FORM FIELDS (only unfilled ones):
${JSON.stringify(slim, null, 1)}`;
}

async function askGemini(fields, ctx) {
  if (FAKE_LLM) return fakeMap(fields, ctx);
  const url = `https://generativelanguage.googleapis.com/v1beta/models/${MODEL}:generateContent`;
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'x-goog-api-key': KEY },
    body: JSON.stringify({
      contents: [{ parts: [{ text: buildPrompt(fields, ctx) }] }],
      generationConfig: { responseMimeType: 'application/json', responseSchema: SCHEMA, temperature: 0 },
    }),
  });
  const data = await res.json();
  if (data.error) throw new Error(`Gemini: ${data.error.message}`);
  return JSON.parse(data.candidates[0].content.parts[0].text);
}

// Tests only: simple keyword mapping so the browser side can be exercised without an API key.
function fakeMap(fields, ctx) {
  const p = ctx.profile, actions = [], needs_you = [];
  const rules = [[/first/i, p.firstName], [/last/i, p.lastName], [/e-?mail/i, p.email], [/phone/i, p.phone],
    [/linkedin/i, p.linkedin], [/github/i, p.github], [/portfolio|website/i, p.portfolio], [/city|location/i, p.location]];
  for (const f of fields) {
    const t = `${f.label} ${f.question} ${f.name}`;
    if (f.type === 'file') continue;
    if (SENSITIVE.test(t)) { needs_you.push({ key: f.key, question: f.label || f.question, reason: 'personal choice' }); continue; }
    const l = ctx.learned.find((x) => norm(x.question) === norm(f.question || f.label || f.name));
    if (l) {
      if (f.type === 'radio' || f.type === 'checkbox') { if (norm(f.option) === norm(l.answer)) actions.push({ key: f.key, action: 'check', value: 'true' }); continue; }
      actions.push({ key: f.key, action: f.tag === 'select' ? 'select' : 'type', value: l.answer }); continue;
    }
    const r = rules.find(([re]) => re.test(t));
    if (r && r[1]) { actions.push({ key: f.key, action: f.tag === 'select' ? 'select' : 'type', value: r[1] }); continue; }
    const qa = ctx.qa.find((x) => t.toLowerCase().includes(x.q.toLowerCase().slice(0, 25)));
    if (qa) actions.push({ key: f.key, action: 'type', value: qa.a });
    else if (f.tag === 'textarea' || f.required) needs_you.push({ key: f.key, question: f.label || f.question, reason: 'no saved answer' });
  }
  return { actions, needs_you };
}

// ---------- filling ----------

async function fill(field, action, value) {
  const sel = `[data-af-id="${field.id}"]`;
  const el = await field.frame.$(sel);
  if (!el) throw new Error('field disappeared');
  if (action === 'select') {
    const ok = await field.frame.evaluate((s, v) => {
      const e = document.querySelector(s);
      const opt = Array.from(e.options).find((o) => o.value === v) || Array.from(e.options).find((o) => o.text.trim() === v);
      if (!opt) return false;
      e.value = opt.value;
      e.dispatchEvent(new Event('input', { bubbles: true }));
      e.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    }, sel, value);
    if (!ok) throw new Error(`no option "${value}"`);
  } else if (action === 'check') {
    const checked = await el.evaluate((e) => e.checked);
    if (!checked) await el.click();
  } else {
    await el.evaluate((e) => { e.focus(); e.value = ''; e.dispatchEvent(new Event('input', { bubbles: true })); });
    await el.type(value, { delay: 12 });
    if (field.role === 'combobox') { await new Promise((r) => setTimeout(r, 400)); await el.press('Enter'); }
    await el.evaluate((e) => e.dispatchEvent(new Event('change', { bubbles: true })));
  }
}

async function attachResume(fields, resumePath) {
  const files = fields.filter((f) => f.type === 'file' && !f.filled);
  if (!files.length) return false;
  const target = files.find((f) => /resume|cv/i.test(`${f.label} ${f.question} ${f.name}`))
    || (files.length === 1 && !/cover/i.test(`${files[0].label} ${files[0].question} ${files[0].name}`) ? files[0] : null);
  if (!target) return false;
  const el = await target.frame.$(`[data-af-id="${target.id}"]`);
  await el.uploadFile(resumePath);
  await el.evaluate((e) => e.dispatchEvent(new Event('change', { bubbles: true })));
  log(`ATTACHED: ${path.basename(resumePath)} -> "${target.label || target.question || target.name || 'file upload'}"`);
  return true;
}

// ---------- learning from what the user types ----------

async function currentValue(f) {
  return f.frame.evaluate((id, type) => {
    const e = document.querySelector(`[data-af-id="${id}"]`);
    if (!e) return null;
    if (type === 'radio' || type === 'checkbox') {
      const group = e.name ? Array.from(document.querySelectorAll(`input[name="${CSS.escape(e.name)}"]`)) : [e];
      const on = group.filter((x) => x.checked);
      if (!on.length) return '';
      const lbl = (x) => {
        let t = x.id && document.querySelector(`label[for="${CSS.escape(x.id)}"]`);
        t = t ? t.innerText : (x.closest('label') ? x.closest('label').innerText : x.value);
        return (t || '').replace(/\s+/g, ' ').trim();
      };
      return on.map(lbl).join(', ');
    }
    if (e.tagName === 'SELECT') return e.selectedIndex > 0 ? e.options[e.selectedIndex].text.trim() : (e.value ? e.options[e.selectedIndex].text.trim() : '');
    return (e.value || '').trim();
  }, f.id, f.type);
}

function isJobSpecific(f, value, rec) {
  const q = `${f.question} ${f.label}`.toLowerCase();
  const company = (rec.company || '').toLowerCase();
  return f.tag === 'textarea' || value.length > 200 || (company && q.includes(company))
    || /why (do )?you want|why are you interested|why (this|our) (company|role|team)|cover letter/i.test(q);
}

function saveLearned(question, answer, jobId) {
  const profile = JSON.parse(fs.readFileSync(PROFILE_PATH, 'utf8'));
  profile.learned = profile.learned || [];
  const k = norm(question);
  const cur = profile.learned.find((x) => norm(x.question) === k);
  if (cur && cur.answer === answer) return false;
  if (cur) Object.assign(cur, { answer, updated: new Date().toISOString().slice(0, 10), from_job: Number(jobId) });
  else profile.learned.push({ question, answer, updated: new Date().toISOString().slice(0, 10), from_job: Number(jobId) });
  const tmp = PROFILE_PATH + '.tmp';
  fs.writeFileSync(tmp, JSON.stringify(profile, null, 2) + '\n');
  fs.renameSync(tmp, PROFILE_PATH);
  return true;
}

function saveJobAnswer(jobId, question, answer) {
  const rec = JSON.parse(tracker('show', jobId));
  const cur = (rec.qa || []).find((x) => norm(x.q) === norm(question));
  if (cur && cur.a === answer) return false;
  const file = path.join(require('os').tmpdir(), `autofill-qa-${process.pid}.json`);
  fs.writeFileSync(file, JSON.stringify([cur ? { id: cur.id, a: answer } : { q: question, a: answer }]));
  try { tracker('qa-add', jobId, '--file', file); } finally { fs.unlinkSync(file); }
  return true;
}

// Fields worth watching: ones left for the user, and ones we filled (to catch their corrections).
function watchable(f) {
  const t = `${f.label} ${f.question} ${f.name}`;
  return f.type !== 'file' && !SENSITIVE.test(t) && !NO_LEARN.test(t) && (f.question || f.label || f.name);
}

async function learnRound(watch, rec, id) {
  for (const [key, w] of watch) {
    let v;
    try { v = await currentValue(w.f); } catch (_) { watch.delete(key); continue; } // page moved on
    if (v === null) { watch.delete(key); continue; }
    if (!v || v === w.ours) { w.last = v; w.stable = 0; continue; }
    if (v !== w.last) { w.last = v; w.stable = 0; continue; }
    if (++w.stable < 2 || v === w.saved) continue;   // same value for ~4s: they're done typing
    w.saved = v;
    const question = (w.f.type === 'radio' || w.f.type === 'checkbox') ? (w.f.question || w.f.name) : (w.f.question && w.f.tag === 'textarea' ? w.f.question : (w.f.label || w.f.question || w.f.name));
    try {
      if (isJobSpecific(w.f, v, rec)) {
        if (saveJobAnswer(id, question, v)) log(`LEARNED: "${question}" saved to job #${id}'s answers`);
      } else if (saveLearned(question, v, id)) {
        log(`LEARNED: "${question}" = ${v.length > 60 ? v.slice(0, 57) + '...' : v} (saved to your profile)`);
      }
    } catch (e) { log(`WARN: couldn't save what you entered for "${question}": ${e.message}`); }
  }
}

// ---------- main ----------

async function main() {
  const id = process.argv[2];
  if (!id || !/^\d+$/.test(id)) {
    log('Usage: node tools/apply/autofill.js <tracker record id> [--url <application url>]');
    process.exit(64);
  }
  if (!KEY && !FAKE_LLM) {
    log('FAILED: GEMINI_API_KEY is not set. Run:  export GEMINI_API_KEY="your key"   (never hardcode it in a file)');
    process.exit(1);
  }
  let puppeteer;
  try { puppeteer = require('puppeteer'); } catch (_) {
    log('FAILED: Puppeteer is not installed. Run:  cd tools/apply && npm install');
    process.exit(1);
  }

  const rec = JSON.parse(tracker('show', id));
  const url = arg('--url') || rec.url;
  if (!url) { log(`FAILED: job #${id} has no link. Pass one with --url.`); process.exit(1); }
  const resumePath = rec.resume ? path.join(ROOT, rec.resume) : null;
  if (!resumePath || !fs.existsSync(resumePath)) { log(`FAILED: resume PDF not found for job #${id} (${rec.resume || 'none'}).`); process.exit(1); }
  const profilePath = path.join(ROOT, 'base', 'profile.json');
  if (!fs.existsSync(profilePath)) { log('FAILED: base/profile.json is missing.'); process.exit(1); }
  const profile = JSON.parse(fs.readFileSync(profilePath, 'utf8'));
  const { learned = [], _note, ...profileCore } = profile;
  const ctx = {
    profile, profileCore,
    learned: learned.map(({ question, answer }) => ({ question, answer })),
    job: { company: rec.company, role: rec.role, location: rec.location },
    qa: (rec.qa || []).map(({ q, a }) => ({ q, a })),
    resumeText: (rec.resume_text || '').slice(0, 6000),
  };

  log(`Job #${id}: ${rec.company} - ${rec.role}`);
  log(`Resume: ${rec.resume}`);
  log(`Opening ${url}`);
  const browser = await puppeteer.launch({
    headless: HEADLESS, defaultViewport: null, userDataDir: HEADLESS ? undefined : PROFILE_DIR,
    executablePath: process.env.PUPPETEER_EXECUTABLE_PATH || undefined,
    args: HEADLESS ? ['--no-sandbox'] : ['--start-maximized'],  // tests run as root in a container
  });
  const page = (await browser.pages())[0] || (await browser.newPage());
  let closed = false;
  browser.on('disconnected', () => { closed = true; });
  await page.goto(url, { waitUntil: 'networkidle2', timeout: 60000 }).catch((e) => log(`WARN: ${e.message}`));
  log('Watching the page. Click "Apply" if needed; new fields are filled as they appear. Review everything, then submit yourself.');

  const seen = new Set();
  const watch = new Map();   // fields to learn from
  let resumeAttached = false, markedApplied = false, rounds = 0;
  while (!closed) {
    let scan;
    try { scan = await scanAll(page); } catch (_) { await sleep(1500); continue; }

    if (!markedApplied && CONFIRMED.test(scan.pageText)) {
      markedApplied = true;
      try { tracker('update', id, '--status', 'Applied'); log(`SUBMITTED: confirmation detected; job #${id} marked Applied.`); }
      catch (e) { log(`WARN: could not update the tracker: ${e.message}`); }
    }
    await learnRound(watch, rec, id);
    const fresh = scan.fields.filter((f) => !f.filled && !seen.has(`${f.frame.url()}|${f.key}|${f.label}|${f.name}`));
    if (fresh.length) {
      fresh.forEach((f) => seen.add(`${f.frame.url()}|${f.key}|${f.label}|${f.name}`));
      rounds++;
      if (!resumeAttached) resumeAttached = await attachResume(fresh, resumePath).catch((e) => { log(`WARN: resume upload: ${e.message}`); return false; });
      const toMap = fresh.filter((f) => f.type !== 'file');
      // radio/checkbox groups are watched once, through their first option
      const groups = new Set();
      for (const f of toMap) {
        if (!watchable(f)) continue;
        const g = (f.type === 'radio' || f.type === 'checkbox') && f.name ? `${f.frame.url()}|${f.name}` : f.key;
        if (groups.has(g)) continue;
        groups.add(g);
        watch.set(f.key, { f, ours: null, last: '', stable: 0, saved: null });
      }
      // sensitive questions never go to the model
      const sensitive = toMap.filter((f) => SENSITIVE.test(`${f.label} ${f.question} ${f.name}`));
      const rest = toMap.filter((f) => !sensitive.includes(f));
      const reported = new Set();
      sensitive.forEach((f) => {
        const q = f.question || f.label || f.name;
        if (!reported.has(q)) { reported.add(q); log(`NEEDS YOU: ${q} (personal choice)`); }
      });
      if (rest.length) {
        log(`Round ${rounds}: mapping ${rest.length} field(s)...`);
        try {
          const plan = await askGemini(rest, ctx);
          const byKey = new Map(rest.map((f) => [f.key, f]));
          let n = 0;
          for (const a of plan.actions || []) {
            const f = byKey.get(a.key);
            if (!f || SENSITIVE.test(`${f.label} ${f.question} ${f.name}`)) continue;
            try {
              await fill(f, a.action, a.value); n++;
              const w = watch.get(f.key) || [...watch.values()].find((x) => x.f.name && x.f.name === f.name && x.f.frame === f.frame);
              if (w) { try { w.ours = await currentValue(w.f); } catch (_) { /* ignore */ } } log(`FILLED: ${f.label || f.question || f.name} = ${a.value.length > 80 ? a.value.slice(0, 77) + '...' : a.value}`);
            }
            catch (e) { log(`SKIPPED: ${f.label || f.question || f.name} (${e.message})`); }
          }
          for (const q of plan.needs_you || []) {
            if (!reported.has(q.question)) { reported.add(q.question); log(`NEEDS YOU: ${q.question} (${q.reason})`); }
          }
          log(`Round ${rounds}: filled ${n} field(s).`);
        } catch (e) { log(`WARN: ${e.message}`); }
      }
      if (!resumeAttached && fresh.some((f) => f.type === 'file')) log('NEEDS YOU: attach the resume yourself: ' + resumePath);
    }
    await sleep(2000);
  }
  if (watch.size) log(`Learned answers are kept in base/profile.json ("learned") and in each job's saved answers.`);
  log(markedApplied ? 'Browser closed. Job marked Applied.' : 'Browser closed. If you submitted, mark the job Applied in the tracker.');
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

main().catch((e) => { log(`FAILED: ${e.message}`); process.exit(1); });
