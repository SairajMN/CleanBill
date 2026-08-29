const $ = id => document.getElementById(id);
let files = { bill: [], eob: [] }, selected = null;

const DROP = { bill: "dropbill", eob: "dropeob", input: { bill: "filesbill", eob: "fileseob" }, list: { bill: "filelistbill", eob: "filelisteob" } };
for (const kind of ["bill", "eob"]) {
  const drop = $(DROP[kind]);
  drop.onclick = () => $(DROP.input[kind]).click();
  drop.ondragover = e => { e.preventDefault(); drop.classList.add("over"); };
  drop.ondragleave = () => drop.classList.remove("over");
  drop.ondrop = e => { e.preventDefault(); drop.classList.remove("over"); addFiles(kind, e.dataTransfer.files); };
  $(DROP.input[kind]).onchange = e => addFiles(kind, e.target.files);
}
function addFiles(kind, list) {
  for (const f of list) files[kind].push(f);
  $(DROP.list[kind]).textContent = files[kind].map(f => f.name).join(", ");
}

$("go").onclick = async () => {
  const hasBill = files.bill.length || $("textbill").value.trim();
  const hasEob = files.eob.length || $("texteob").value.trim();
  if (!hasBill && !hasEob) { $("err").textContent = "Add a bill or EOB file, or paste the text."; return; }
  $("err").textContent = ""; $("spinner").style.display = "block"; $("go").disabled = true;
  const fd = new FormData();
  files.bill.forEach(f => fd.append("bill_files", f));
  files.eob.forEach(f => fd.append("eob_files", f));
  fd.append("bill_text", $("textbill").value);
  fd.append("eob_text", $("texteob").value);
  try {
    const r = await fetch("/api/analyze", { method: "POST", body: fd });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || "analysis failed");
    files = { bill: [], eob: [] };
    for (const kind of ["bill", "eob"]) { $(DROP.list[kind]).textContent = ""; $(DROP.input[kind]).value = ""; }
    $("textbill").value = ""; $("texteob").value = "";
    await loadCases(); showCase(j.case_id);
  } catch (e) { $("err").textContent = e.message; }
  $("spinner").style.display = "none"; $("go").disabled = false;
};

async function loadCases() {
  const cases = await (await fetch("/api/cases")).json();
  $("caselist").innerHTML = cases.map(c =>
    `<li onclick="showCase('${c.id}')" class="${c.id === selected ? "sel" : ""}">
       <span>#${c.id.slice(0, 6)} · ${c.discrepancies} issue${c.discrepancies === 1 ? "" : "s"}</span>
       <span class="badge ${c.state}">${c.state}</span></li>`).join("");
}

function itemsTable(rows, now) {
  return `<tr><th>Service</th><th>Code</th><th>Billed</th></tr>` +
    rows.map(i => `<tr><td>${i.description}</td><td>${i.code || "—"}</td><td>$${i.amount_billed.toFixed(2)}</td></tr>`).join("");
}

async function showCase(id) {
  selected = id;
  const c = await (await fetch("/case/" + id)).json();
  $("placeholder").classList.add("hidden");
  $("casebody").classList.remove("hidden");
  $("caseid").textContent = "case " + id;
  $("statebadge").className = "badge " + c.state; $("statebadge").textContent = c.state;
  $("confidence").textContent = c.pricing_confidence != null ? "confidence " + (c.pricing_confidence * 100).toFixed(0) + "%" : "";
  $("disclist").innerHTML = (c.discrepancies || []).length
    ? c.discrepancies.map(d =>
        `<div class="disc"><b>${d.issue_type.replace("_", " ")}</b> — $${d.amount_disputed.toFixed(2)}<br>${d.description}</div>`).join("")
    : '<p class="ok">No errors found — nothing to dispute.</p>';
  const docs = c.docs || {};
  $("items").innerHTML = docs.bill && docs.bill.line_items ? itemsTable(docs.bill.line_items) : "";
  $("eobitems").innerHTML = docs.eob && docs.eob.line_items
    ? `<tr><th>Service</th><th>Code</th><th>Billed</th><th>Allowed</th></tr>` +
      docs.eob.line_items.map(i => `<tr><td>${i.description}</td><td>${i.code || "—"}</td><td>$${i.amount_billed.toFixed(2)}</td><td>$${i.insurance_paid.toFixed(2)}</td></tr>`).join("")
    : "";
  $("letter").textContent = c.letter ? c.letter.subject + "\n\n" + c.letter.body : "(no draft — nothing worth disputing)";
  const canSend = c.state === "pending_approval" && !!c.letter;
  $("send").disabled = !canSend;
  $("sendnote").textContent = c.state === "awaiting_reply" ? "Sent — follow-up scheduled" :
    canSend ? "You approve before anything is sent" :
    c.state === "pending_approval" ? "drafting…" : "";
}

$("send").onclick = async () => {
  $("send").disabled = true;
  const r = await fetch("/approve/" + selected, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approver: "web-user" })
  });
  const j = await r.json();
  if (!r.ok) { $("sendnote").textContent = j.detail || "send failed"; $("send").disabled = false; return; }
  showCase(selected); loadCases();
};

loadCases();
setInterval(loadCases, 15000);
