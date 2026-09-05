"use strict";

// No storage, analytics, remote resources, HTML interpolation, or model calls.
// User-controlled content enters the DOM only through textContent/value.
const byId = (id) => document.getElementById(id);
const storyInput = byId("story");
const form = byId("story-form");
const buildButton = byId("build-button");
const exampleButton = byId("example-button");
const clearButton = byId("clear-button");
const bundleButton = byId("bundle-download");
const fileButton = byId("file-download");
const tabs = Array.from(document.querySelectorAll('#view-tabs [role="tab"]'));
const fileButtons = Array.from(document.querySelectorAll("#file-picker button"));
const kinds = {
  evm_tx_hash: "EVM transaction hash", btc_txid: "Bitcoin transaction ID",
  evm_address: "EVM wallet address", btc_address: "Bitcoin wallet address",
  amount: "Amount", date: "Date", url: "Website", email: "Email",
  exchange: "Exchange / platform", business: "Business candidate", phone: "Phone",
};
let review = null;
let builtStory = "";
let exampleStory = "";
let busy = false;
let downloading = false;
let selectedFile = "ic3_draft.md";

function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
}

function status(message) {
  byId("status-message").textContent = message;
}

function error(message) {
  byId("error-message").textContent = message;
  byId("error-message").hidden = !message;
}

function synchronize() {
  const stale = review !== null && storyInput.value !== builtStory;
  const canDownload = review !== null && review.audit.passed && !stale && !busy && !downloading;
  byId("stale-note").hidden = !stale;
  byId("story-count").textContent = `${storyInput.value.length.toLocaleString("en-US")} / 16,000 characters`;
  byId("example-label").hidden = !exampleStory || storyInput.value !== exampleStory;
  buildButton.disabled = busy;
  exampleButton.disabled = busy;
  clearButton.disabled = busy || (!storyInput.value && !review);
  bundleButton.disabled = !canDownload;
  fileButton.disabled = !canDownload;
  buildButton.firstElementChild.textContent = busy ? "Preparing your record…" : review ? "Rebuild my case file" : "Build my case file";
  bundleButton.firstChild.textContent = downloading ? "Preparing download… " : "Download bundle ";
  byId("case-results").setAttribute("aria-busy", String(busy));
}

async function post(path, story) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Recourse-Request": "1" },
    body: JSON.stringify({ story }),
    cache: "no-store",
    credentials: "omit",
  });
  if (!response.ok) {
    let message = "The local workbench could not complete this request.";
    try { message = (await response.json()).error || message; } catch { /* Keep a plain local error. */ }
    throw new Error(message);
  }
  return response;
}

function selectTab(tab, moveFocus = false) {
  for (const candidate of tabs) {
    const selected = candidate === tab;
    candidate.setAttribute("aria-selected", String(selected));
    candidate.tabIndex = selected ? 0 : -1;
    byId(candidate.getAttribute("aria-controls")).hidden = !selected;
  }
  if (moveFocus) tab.focus();
}

tabs.forEach((tab, index) => {
  tab.addEventListener("click", () => selectTab(tab));
  tab.addEventListener("keydown", (event) => {
    let next;
    if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
    if (event.key === "ArrowLeft") next = (index + tabs.length - 1) % tabs.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = tabs.length - 1;
    if (next !== undefined) {
      event.preventDefault();
      selectTab(tabs[next], true);
    }
  });
});

function sourceContext(text) {
  const details = node("details", "source-context");
  details.append(node("summary", "", "Surrounding text"), node("p", "", text));
  return details;
}

function renderEvidence() {
  const list = byId("evidence-list");
  list.replaceChildren();
  if (!review) return;
  const query = byId("evidence-search").value.toLowerCase().trim();
  const items = review.case.evidence.filter((item) =>
    [kinds[item.kind], item.value, item.verbatim, item.context].join(" ").toLowerCase().includes(query));
  byId("filter-status").textContent = `${items.length} of ${review.case.evidence.length} source items`;
  if (!items.length) {
    list.append(node("li", "no-evidence", query
      ? "No evidence matches this search. Try another value or clear the filter."
      : "No supported details were extracted. Your story is still preserved in the case record. Open Review gaps for what to add next."));
  }
  for (const item of items) {
    const card = node("li", "evidence-item");
    const heading = node("div", "evidence-item-heading");
    heading.append(node("span", "evidence-kind", kinds[item.kind] || item.kind),
      node("span", "source-label", "VERBATIM QUOTE BELOW"));
    card.append(heading, node("code", "evidence-value", item.value), node("blockquote", "source-quote", item.verbatim));
    const locate = node("button", "text-button", "Locate quote in story ↗");
    locate.type = "button";
    locate.addEventListener("click", () => {
      if (storyInput.value !== builtStory) {
        status("These results are from your previous story. Rebuild before locating a quote.");
        return;
      }
      const start = storyInput.value.indexOf(item.verbatim);
      if (start >= 0) {
        storyInput.focus();
        storyInput.setSelectionRange(start, start + item.verbatim.length);
        status(`Selected the source quote for ${kinds[item.kind] || item.kind} in your story.`);
      }
    });
    card.append(locate);
    if (item.context) card.append(sourceContext(item.context));
    list.append(card);
  }
}

function renderTimeline() {
  const list = byId("transaction-list");
  list.replaceChildren();
  byId("timeline-count").textContent = `(${review.case.transactions.length})`;
  if (!review.case.transactions.length) {
    list.append(node("li", "no-evidence", "No transfers could be reconstructed. Include an amount and the details for each transfer in separate paragraphs."));
  }
  review.case.transactions.forEach((transfer, index) => {
    const item = node("li", "transfer");
    const amount = transfer.amount === null ? "Amount not provided" : `${transfer.amount} ${transfer.currency || "[currency not provided]"}`;
    item.append(node("span", "transfer-date", transfer.date || "Date not provided"), node("h4", "", `Transfer ${index + 1} · ${amount}`));
    const fields = node("dl");
    for (const [label, value] of [["Payment method", transfer.method], ["Asset sent", transfer.asset || transfer.currency],
      ["Transaction hash / ID", transfer.tx_hash], ["Destination address", transfer.destination_address]]) {
      fields.append(node("dt", "", label), node("dd", "", value || "[NOT PROVIDED]"));
    }
    item.append(fields);
    if (transfer.context) item.append(sourceContext(transfer.context));
    list.append(item);
  });
}

function fileContents(filename) {
  return filename === "casefile.json"
    ? JSON.stringify(review.case, null, 2) + "\n"
    : review.documents[filename] + "\n";
}

function renderDocument(filename = selectedFile) {
  selectedFile = filename;
  fileButtons.forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.file === filename)));
  byId("document-name").textContent = filename;
  byId("document-content").textContent = review ? fileContents(filename) : "";
  byId("document-content").scrollTop = 0;
}

fileButtons.forEach((button) => button.addEventListener("click", () => renderDocument(button.dataset.file)));
byId("evidence-search").addEventListener("input", renderEvidence);

function renderReview() {
  const { case: casefile, audit } = review;
  byId("empty-desk").hidden = true;
  byId("case-results").hidden = false;
  byId("case-kind").textContent = builtStory === exampleStory ? "FICTIONAL EXAMPLE · CASE RECORD" : "YOUR CASE RECORD";
  byId("case-id").textContent = `CASE ${casefile.case_id.slice(0, 12)} · Full ID in Case JSON`;
  byId("case-title").textContent = audit.passed ? "Ready for your review." : "Source checks need attention.";
  byId("evidence-count").textContent = casefile.evidence.length;
  byId("transaction-count").textContent = casefile.transactions.length;
  byId("note-count").textContent = casefile.unverified_notes.length;
  byId("tab-evidence-count").textContent = casefile.evidence.length;
  byId("tab-note-count").textContent = casefile.unverified_notes.length;
  byId("audit-title").textContent = audit.passed ? "Source checks passed · nothing independently verified" : "Source checks failed · downloads blocked";
  byId("audit-banner").classList.toggle("failed", !audit.passed);
  byId("audit-banner").open = !audit.passed;
  byId("audit-violations").replaceChildren(...audit.violations.map((violation) => node("li", "", violation)));
  byId("audit-violations").hidden = audit.passed;
  byId("notes-list").replaceChildren(...casefile.unverified_notes.map((note) => node("li", "", note)));
  byId("evidence-search").value = "";
  byId("timeline").open = false;
  renderEvidence();
  renderTimeline();
  renderDocument("ic3_draft.md");
  selectTab(tabs[0]);
  synchronize();
  byId("case-title").focus();
}

storyInput.addEventListener("input", () => { error(""); status(""); synchronize(); });
storyInput.addEventListener("keydown", (event) => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter" && !busy) {
    event.preventDefault();
    form.requestSubmit();
  }
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  const story = storyInput.value;
  if (!story.trim()) {
    error("Add a few words about what happened, or try the fictional example.");
    storyInput.focus();
    return;
  }
  busy = true;
  error("");
  status("Extracting details and checking their sources on your computer…");
  synchronize();
  try {
    const response = await post("/api/case", story);
    review = await response.json();
    builtStory = story;
    renderReview();
    status(review.audit.passed
      ? "Your record is ready to review. Nothing has been filed or sent."
      : "Review the source-check notes. Downloads are blocked until the audit passes.");
  } catch (problem) {
    status("");
    error(problem instanceof TypeError
      ? "The local server could not be reached. Keep this page open, check that Recourse is running in your terminal, and try again."
      : problem.message);
  } finally {
    busy = false;
    synchronize();
  }
});

exampleButton.addEventListener("click", async () => {
  if (busy) return;
  busy = true;
  error("");
  synchronize();
  try {
    const response = await fetch("/api/example", { cache: "no-store", credentials: "omit" });
    if (!response.ok) throw new Error("Could not load the fictional example.");
    const example = await response.json();
    exampleStory = example.story;
    storyInput.value = exampleStory;
    status("Fictional example loaded. Build the case file to explore its evidence and drafts.");
    storyInput.focus();
  } catch {
    error("Could not load the example. Check that the local Recourse server is running.");
  } finally {
    busy = false;
    synchronize();
  }
});

clearButton.addEventListener("click", () => {
  storyInput.value = "";
  review = null;
  builtStory = "";
  byId("case-results").hidden = true;
  byId("empty-desk").hidden = false;
  for (const id of ["evidence-list", "notes-list", "transaction-list", "document-content", "audit-violations", "case-id"]) byId(id).replaceChildren();
  byId("evidence-search").value = "";
  error("");
  status("Story and case cleared from this page.");
  synchronize();
  storyInput.focus();
});

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = node("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  // Let the browser begin the download before releasing the in-memory URL.
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

fileButton.addEventListener("click", () => {
  if (!review || !review.audit.passed || storyInput.value !== builtStory || busy || downloading) return;
  saveBlob(new Blob([fileContents(selectedFile)], { type: selectedFile.endsWith(".json") ? "application/json" : "text/markdown;charset=utf-8" }), selectedFile);
  status("File download started. Check your browser's downloads for the draft.");
});

bundleButton.addEventListener("click", async () => {
  if (!review || !review.audit.passed || storyInput.value !== builtStory || busy || downloading) return;
  const submittedStory = builtStory;
  const submittedId = review.case.case_id;
  downloading = true;
  error("");
  synchronize();
  try {
    const response = await post("/api/bundle", submittedStory);
    const blob = await response.blob();
    if (!review || storyInput.value !== submittedStory || builtStory !== submittedStory) {
      status("Your story changed while preparing the bundle. Rebuild before downloading.");
      return;
    }
    saveBlob(blob, `recourse-${submittedId.slice(0, 12)}-drafts.zip`);
    status("Bundle download started. It contains your case record and four drafts; nothing has been filed.");
  } catch (problem) {
    error(problem instanceof TypeError ? "The local server could not be reached. Check your terminal and try again." : problem.message);
  } finally {
    downloading = false;
    synchronize();
  }
});

synchronize();
