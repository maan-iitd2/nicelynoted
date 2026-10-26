// Attaches the meeting transcript to a new ChatGPT chat, fills the prompt and sends it.
// ChatGPT's markup changes often and differs by layout, so every lookup tries several
// selectors; when nothing matches it dumps what it did find, to fix the list.
(async () => {
  const { pending } = await chrome.storage.local.get("pending");
  if (!pending) return;
  console.log("NicelyNoted: pending meeting found,", pending.text.length, "chars");
  await chrome.storage.local.remove("pending");   // a failed run must not re-send on a later visit

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const waitFor = (fn, ms) => new Promise((resolve) => {
    const t0 = Date.now();
    const iv = setInterval(() => {
      const v = fn();
      if (v || Date.now() - t0 > ms) { clearInterval(iv); resolve(v); }
    }, 500);
  });
  const visible = (e) => e.getClientRects().length > 0;
  const COMPOSER = '#prompt-textarea, [id$="composer-prompt"], textarea[aria-label*="Chat with ChatGPT" i], div.ProseMirror[contenteditable="true"], [contenteditable="true"][role="textbox"]';
  const SENDS = ['button[data-testid="send-button"], #composer-submit-button, button[aria-label="Send message"]',
                 'button[aria-label*="Send" i], button[type="submit"]'];   // broad net only if the exact ones are absent

  const box = await waitFor(() => [...document.querySelectorAll(COMPOSER)].find(visible), 60000);
  if (!box) {
    return console.warn("NicelyNoted: composer not found. Editable elements on page:",
      [...document.querySelectorAll('textarea, [contenteditable]')].map((e) => `${e.tagName}#${e.id}[${e.getAttribute("aria-label")}]`));
  }
  console.log("NicelyNoted: composer found", box.tagName, box.id);

  const file = new File([pending.text], pending.name, { type: "text/plain" });
  const files = new DataTransfer();
  files.items.add(file);

  box.focus();
  const inputs = [...document.querySelectorAll('input[type="file"]')];
  // image-only inputs exist for the camera/photos buttons; prefer one that takes anything
  const input = inputs.find((i) => !i.accept || /text|\*\/\*|\.txt/.test(i.accept)) || inputs.find((i) => !/^image/.test(i.accept)) || inputs[0];
  if (input) {
    console.log("NicelyNoted: attaching via file input", input.id, input.accept.slice(0, 30) || "(any)");
    input.files = files.files;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  } else {
    console.log("NicelyNoted: no file input; attaching via paste");
    box.dispatchEvent(new ClipboardEvent("paste", { clipboardData: files, bubbles: true, cancelable: true }));
  }
  await sleep(3000);   // let the attachment card appear before the text goes in

  box.focus();
  if (box.tagName === "TEXTAREA") {   // plain textarea: React only listens to the native setter + input event
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set.call(box, pending.prompt);
    box.dispatchEvent(new Event("input", { bubbles: true }));
  } else {
    const text = new DataTransfer();
    text.setData("text/plain", pending.prompt);
    box.dispatchEvent(new ClipboardEvent("paste", { clipboardData: text, bubbles: true, cancelable: true }));
    if (!box.textContent.trim()) document.execCommand("insertText", false, pending.prompt);
  }
  console.log("NicelyNoted: prompt entered,", (box.value ?? box.textContent).length, "chars");

  // the send button stays disabled while the upload is still going
  const send = await waitFor(() => {
    for (const sel of SENDS) {
      const b = [...document.querySelectorAll(sel)].find((e) => visible(e) && !e.disabled);
      if (b) return b;
    }
  }, 90000);
  const text = () => (box.value ?? box.textContent).trim();
  if (send) {
    console.log("NicelyNoted: clicking send", send.getAttribute("aria-label") || send.id);
    send.click();
    await sleep(2500);
  } else {
    console.warn("NicelyNoted: no enabled send button; visible buttons:",
      [...document.querySelectorAll("button")].filter(visible).map((e) => e.getAttribute("aria-label") || e.id || e.textContent.trim().slice(0, 20)).filter(Boolean));
  }
  if (text()) {   // still in the composer = the click did nothing; submit like a keypress would
    console.log("NicelyNoted: composer still has text, pressing Enter");
    box.focus();
    for (const type of ["keydown", "keypress", "keyup"]) {
      box.dispatchEvent(new KeyboardEvent(type, { key: "Enter", code: "Enter", keyCode: 13, which: 13, bubbles: true, cancelable: true }));
    }
  }
})();
