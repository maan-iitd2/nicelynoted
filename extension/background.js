// Meet tab opens -> start NicelyNoted's recorder (native host). Last Meet tab closes or leaves
// the call -> stop it, then open ChatGPT with the transcript (chatgpt.js fills the box).
const HOST = "com.nicelynoted.host";
const CALL = /^https:\/\/meet\.google\.com\/(?:meet\/)?[a-z]{3}-[a-z]{4}-[a-z]{3}/;
const PROMPT = `The attached file is the transcript of a meeting I just attended. It was auto-generated from audio, so expect some mistakes. "You" is me (my mic); "Them" is everyone else mixed together, speakers are not separated.

Please give me:
1. Summary: 3-5 sentences on what the meeting was about and what was decided.
2. Meeting notes: the key points and decisions, as short bullets.
3. Tasks: every action item with the owner and deadline if mentioned (say "unassigned" if not), and flag which ones are mine.`;

let port = null;
const callTabs = new Set();

const badge = (text) => chrome.action.setBadgeText({ text });

function start() {
  if (port) return;
  console.log("NicelyNoted: starting host");
  let p;
  try {
    p = port = chrome.runtime.connectNative(HOST);
  } catch (e) {
    console.error("NicelyNoted: connectNative threw", e);
    return badge("ERR");
  }
  p.onMessage.addListener((m) => {
    if (m.transcript === undefined) return;
    p.disconnect();
    badge("");
    if (m.transcript.trim()) openChatGPT(m.path.split("\\").pop(), m.transcript);
  });
  p.onDisconnect.addListener(() => {
    const err = chrome.runtime.lastError?.message;
    if (err) console.warn("NicelyNoted host:", err);
    if (port === p) port = null;
    badge(err ? "ERR" : "");
  });
  p.onMessage.addListener((m) => console.log("NicelyNoted: host says", JSON.stringify(m).slice(0, 200)));
  p.postMessage({ cmd: "start" });
  badge("REC");
}

function stop() {
  const p = port;
  if (!p) return;
  port = null;   // a new call can start while this one finishes transcribing
  badge("...");
  p.postMessage({ cmd: "stop" });
}

async function openChatGPT(name, transcript) {
  await chrome.storage.local.set({ pending: { prompt: PROMPT, name, text: transcript } });
  chrome.tabs.create({ url: "https://chatgpt.com/" });
}

function sync(tabId, url) {
  if (url && url.includes("meet.google.com")) console.log("NicelyNoted: meet tab", url, "call:", CALL.test(url));
  if (url && CALL.test(url)) {
    callTabs.add(tabId);
    start();
  } else if (callTabs.delete(tabId) && !callTabs.size) {
    stop();
  }
}

chrome.tabs.onUpdated.addListener((id, info) => info.url && sync(id, info.url));
chrome.tabs.onRemoved.addListener((id) => sync(id, null));
const scan = () => chrome.tabs.query({}, (tabs) => {
  console.log("NicelyNoted: scanning", tabs.length, "tabs");
  tabs.forEach((t) => sync(t.id, t.url));
});
scan();                                   // already in a call when loaded
chrome.action.onClicked.addListener(scan);   // click the toolbar icon to re-scan by hand
