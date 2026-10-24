/**
 * Morning send from Gmail Drafts (Google Apps Script; runs on Google's servers, so your Mac can be off).
 *
 * Every weekday at 8:00 AM in TIME_ZONE it sends EVERY draft in Gmail Drafts that has a recipient,
 * up to DAILY_CAP per morning, with a short pause between them. Sent emails land in Sent Mail, and the
 * tracker's Cold email tab marks them Sent the next time it syncs.
 *
 * Setup (once): script.google.com > New project > paste this file over Code.gs > Save >
 * choose `setup` in the function menu > Run > allow access to Gmail. To stop it, run `stop`.
 */
const TIME_ZONE = "America/Denver";   // set to yours (a tz name like "America/New_York"); daylight saving is handled
const SEND_HOUR = 8;                   // sends during the 8:00 hour
const DAILY_CAP = 25;                  // at most this many per morning
const PER_RUN = 8;                     // per 5-minute check, so each run stays well under Apps Script's 6-minute limit
const GAP_SECONDS = [15, 30];          // pause between two sends

function sendMorningDrafts() {
  const now = new Date();
  const weekday = Number(Utilities.formatDate(now, TIME_ZONE, "u"));   // 1 = Monday ... 7 = Sunday
  const hour = Number(Utilities.formatDate(now, TIME_ZONE, "H"));
  if (weekday > 5 || hour !== SEND_HOUR) return;

  const today = Utilities.formatDate(now, TIME_ZONE, "yyyy-MM-dd");
  const props = PropertiesService.getScriptProperties();
  let sentToday = props.getProperty("day") === today ? Number(props.getProperty("count") || 0) : 0;
  if (sentToday >= DAILY_CAP) return;

  const lock = LockService.getScriptLock();
  if (!lock.tryLock(1000)) return;     // the previous check is still sending
  try {
    let sentNow = 0;
    for (const draft of GmailApp.getDrafts()) {
      if (sentToday >= DAILY_CAP || sentNow >= PER_RUN) break;
      try {
        const msg = draft.getMessage();
        if (!msg.getTo() && !msg.getCc() && !msg.getBcc()) continue;   // no recipient yet: leave it
        if (sentNow) Utilities.sleep(1000 * (GAP_SECONDS[0] + Math.random() * (GAP_SECONDS[1] - GAP_SECONDS[0])));
        draft.send();
        sentNow++;
        sentToday++;
        props.setProperties({day: today, count: String(sentToday)});
        console.log(`Sent to ${msg.getTo()}: ${msg.getSubject()}`);
      } catch (err) {                  // e.g. the draft was deleted meanwhile: skip it, keep going
        console.warn(`Skipped a draft: ${err}`);
      }
    }
  } finally {
    lock.releaseLock();
  }
}

/** Run once: checks every 5 minutes (it only sends during the 8:00 hour on weekdays). */
function setup() {
  stop();
  ScriptApp.newTrigger("sendMorningDrafts").timeBased().everyMinutes(5).create();
}

/** Turns the morning send off. */
function stop() {
  ScriptApp.getProjectTriggers()
    .filter(t => t.getHandlerFunction() === "sendMorningDrafts")
    .forEach(t => ScriptApp.deleteTrigger(t));
}
