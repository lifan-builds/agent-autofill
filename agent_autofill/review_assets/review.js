/* Local review: source text is rendered as text, never HTML or instructions. */
"use strict";

function parseBatch(text, letters) {
  const proposals = [], errors = [], seen = new Set();
  const clauses = text.split(/[;\n,]+/).map(s => s.trim()).filter(Boolean);
  if (!clauses.length) return {proposals, errors: ["Enter a card letter and a meaning first."]};
  for (const clause of clauses) {
    const match = clause.match(/^([a-z]{1,3})\s+(?:is|=)\s+(.+?)\.?$/i);
    if (!match) { errors.push("Use a card letter followed by ‘is’ and a supported description."); continue; }
    const letter = match[1].toUpperCase(), words = match[2].toLowerCase().trim();
    if (!letters.includes(letter) || seen.has(letter)) { errors.push(`Card ${letter} is unknown or appears more than once.`); continue; }
    seen.add(letter);
    let choice;
    if (/^my current (home|home address)$/.test(words)) choice = {who:"self",role:"home",currentness:"current"};
    else if (/^my current (office|work|work address|office address)$/.test(words)) choice = {who:"self",role:"work",currentness:"current"};
    else if (/^my (old|previous) (home|home address)$/.test(words)) choice = {who:"self",role:"home",currentness:"historical"};
    else if (/^my (old|previous) address$/.test(words)) choice = {who:"self",currentness:"historical"};
    else if (/^my (home|home address)$/.test(words)) choice = {who:"self",role:"home",currentness:"unknown"};
    else if (/^my (office|work|work address)$/.test(words)) choice = {who:"self",role:"work",currentness:"unknown"};
    else if (/^my current (contact details|personal contact)$/.test(words)) choice = {who:"self",role:"contact",currentness:"current"};
    else if (/^(old|an old address|historical)$/.test(words)) choice = {currentness:"historical"};
    else if (/^(someone else's|someone else’s|another person's|another person’s)$/.test(words)) choice = {who:"other",role:"unknown",currentness:"unknown"};
    else { errors.push(`Card ${letter}: use the choices on its card for this description.`); continue; }
    proposals.push({letter, ...choice});
  }
  // A contradictory or unsupported batch never partly applies unnoticed.
  return {proposals: errors.length ? [] : proposals, errors};
}

function draftChoice(group, previous, patch) {
  const next = {...previous, ...patch};
  if (group.reviewed && ["who","role","currentness"].every(k => next[k] === group[k])) return null;
  return {id:group.id,revision:group.revision,who:next.who,role:next.role,currentness:next.currentness};
}
if (typeof module !== "undefined") module.exports = {parseBatch, draftChoice};
if (typeof document !== "undefined") startReview();

function startReview() {
  const $ = id => document.getElementById(id);
  const names = {
    who: {unknown:"Not sure yet",self:"Me",other:"Someone else"},
    role: {unknown:"Not sure yet",home:"Home",work:"Work",contact:"Personal contact",other:"Other"},
    currentness: {unknown:"Not sure yet",current:"Current",historical:"Historical"}
  };
  const drafts = new Map(), skipped = new Set();
  let state = null, task = "delivery", showReviewed = false, limit = 4, saving = false;
  const incoming = new URLSearchParams(location.hash.slice(1)).get("access");
  if (incoming) { sessionStorage.setItem("autofill-review-access", incoming); history.replaceState(null, "", location.pathname); }
  const token = sessionStorage.getItem("autofill-review-access") || "";

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  async function api(path, payload) {
    const options = {headers:{"X-Review-Token":token}};
    if (payload) {
      options.method = "POST";
      options.headers["Content-Type"] = "application/json";
      options.headers["X-Review-Action"] = "confirm";
      options.body = JSON.stringify(payload);
    }
    const response = await fetch(path, options);
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "The review could not be completed.");
    return result;
  }
  function choice(group) { return drafts.get(group.id) || {who:group.who,role:group.role,currentness:group.currentness}; }
  function describe(c) { return `${names.who[c.who]} · ${names.role[c.role]} · ${names.currentness[c.currentness]}`; }
  function stage(group, patch) {
    const next = draftChoice(group, choice(group), patch);
    if (next === null) drafts.delete(group.id);
    else drafts.set(group.id, next);
    skipped.delete(group.id);
    $("message").textContent = "";
    render();
  }
  function values(group) {
    const field = key => group.fields.find(f => f.semantic === key)?.value;
    const name = field("full_name") || [field("first_name"),field("last_name")].filter(Boolean).join(" ");
    const address = [field("street"), [field("city"),field("state"),field("postcode")].filter(Boolean).join(", "),field("country")].filter(Boolean).join("\n");
    const contact = [field("email"),field("phone")].filter(Boolean).join(" · ");
    return {name:name || (group.has_address ? "Saved address" : "Saved details"), address, contact};
  }
  function card(group) {
    const current = choice(group), drafted = drafts.has(group.id), summary = values(group);
    const article = el("article", `card${drafted ? " drafted" : ""}`);
    article.id = `card-${group.id}`;
    article.setAttribute("aria-label", `Group ${group.letter}`);
    const top = el("div", "card-top");
    top.append(el("span","group-letter",group.letter),el("span","card-status",drafted?"DRAFT · NOT SAVED":group.reviewed?"REVIEWED":"NEEDS YOUR CONTEXT"));
    const content = el("div", "card-values");
    content.append(el("h3","",summary.name));
    if (summary.address) content.append(el("p","",summary.address));
    if (summary.contact) content.append(el("p","contact-line",summary.contact));
    const details = el("details","saved-fields"), dl = el("dl");
    details.append(el("summary","",`View all ${group.fields.length} saved fields`));
    group.fields.forEach(f => dl.append(el("dt","",f.label),el("dd","",String(f.value))));
    details.append(dl);
    const meaning = el("div","meaning");
    meaning.append(el("div","meaning-label","WHAT SHOULD YOUR AGENT KNOW?"));
    if (group.blocked) meaning.append(el("p","blocked",group.blocked));
    else {
      const presets = el("div","presets");
      const options = group.has_address ? [
        ["Mine · current home",{who:"self",role:"home",currentness:"current"}],
        ["Mine · current work",{who:"self",role:"work",currentness:"current"}],
        ["Old address",{currentness:"historical"}]
      ] : [
        ["My current contact",{who:"self",role:"contact",currentness:"current"}],
        ["My current work details",{who:"self",role:"work",currentness:"current"}]
      ];
      options.push(["Someone else",{who:"other",role:"unknown",currentness:"unknown"}]);
      for (const [label, patch] of options) {
        const button = el("button","chip",label);
        button.addEventListener("click",() => {stage(group,patch); document.getElementById(`select-${group.id}-who`)?.focus();});
        presets.append(button);
      }
      meaning.append(presets);
      const selects = el("div","meaning-selects");
      for (const [key, title] of [["who","Who is this for?"],["role","What is it?"],["currentness","Still current?"]]) {
        const label = el("label","",title), select = el("select");
        select.id = `select-${group.id}-${key}`;
        select.setAttribute("aria-label", `Group ${group.letter}: ${title}`);
        for (const [value, text] of Object.entries(names[key])) {
          const option = el("option","",text); option.value = value; option.selected = value === current[key]; select.append(option);
        }
        select.addEventListener("change",() => {stage(group,{[key]:select.value}); document.getElementById(select.id)?.focus();});
        label.append(select); selects.append(label);
      }
      meaning.append(selects);
    }
    const bottom = el("div","card-bottom"), skip = el("button","skip-button","Leave for later");
    skip.addEventListener("click",() => {skipped.add(group.id); drafts.delete(group.id); render();});
    bottom.append(el("span","draft-description",drafted?"Ready to review below":group.reviewed?describe(current):"You can leave any answer unknown"),skip);
    meaning.append(bottom); article.append(top,content,details,meaning);
    return article;
  }
  function render() {
    if (!state) return;
    $("stats").textContent = `${state.groups.length} saved groups · ${state.field_count} fields · ${state.reviewed_count} reviewed in this trial`;
    $("source-label").textContent = state.source === "synthetic sample" ? "Synthetic sample · no real data" : "Your profile snapshot · encrypted trial";
    const matching = state.groups.filter(g => (task === "all" || (task === "delivery" ? g.has_address : g.has_contact)));
    const visible = matching.filter(g => !skipped.has(g.id) && (showReviewed || !g.reviewed || drafts.has(g.id)));
    $("cards").replaceChildren(...visible.slice(0,limit).map(card));
    if (!visible.length) {
      const empty = el("div","empty");
      empty.append(el("h3","",matching.length ? "You can stop here." : "No groups for this task yet."),el("p","",matching.length?"Confirmed meanings are saved in this trial. Skipped groups will be available next time.":"Try reviewing everything to find unclassified details."));
      const button = el("button","secondary","Show all groups");
      button.addEventListener("click",() => {task="all"; skipped.clear(); showReviewed=true; setTaskButtons(); render();});
      empty.append(button); $("cards").append(empty);
    }
    $("show-more").hidden = visible.length <= limit;
    $("show-more").textContent = `Show ${Math.min(4,visible.length-limit)} more groups`;
    $("draft-count").textContent = drafts.size ? `${drafts.size} ${drafts.size===1?"meaning":"meanings"} ready to review` : "No changes drafted";
    $("draft-hint").textContent = drafts.size ? "Your values and defaults will stay the same." : "Start with one group. You can skip the rest.";
    $("review-changes").disabled = !drafts.size || saving;
    $("clear-drafts").hidden = !drafts.size;
    $("show-reviewed").textContent = showReviewed ? "Hide reviewed groups" : "Include reviewed groups";
  }
  function setTaskButtons() {
    document.querySelectorAll("[data-task]").forEach(button => {
      const active=button.dataset.task===task; button.classList.toggle("active",active); button.setAttribute("aria-pressed",String(active));
    });
  }
  document.querySelectorAll("[data-task]").forEach(button => button.addEventListener("click",() => {task=button.dataset.task;limit=4;setTaskButtons();render();$("review-title").scrollIntoView({behavior:"smooth",block:"start"});}));
  $("show-reviewed").addEventListener("click",() => {showReviewed=!showReviewed;render();});
  $("show-more").addEventListener("click",() => {limit+=4;render();});
  $("clear-drafts").addEventListener("click",() => {drafts.clear();render();});
  $("apply-bulk").addEventListener("click",() => {
    if (!state) return;
    const parsed = parseBatch($("bulk-input").value,state.groups.map(g=>g.letter));
    if (parsed.errors.length) {$("bulk-message").textContent=`No draft changes applied. ${parsed.errors.join(" ")}`;return;}
    const blocked = parsed.proposals.some(p => state.groups.find(g=>g.letter===p.letter).blocked);
    if (blocked) {$("bulk-message").textContent="No changes applied. One of these groups needs a closer review.";return;}
    parsed.proposals.forEach(({letter,...patch})=>stage(state.groups.find(g=>g.letter===letter),patch));
    $("bulk-message").textContent=`Drafted ${parsed.proposals.length} meanings. Check the cards, then review your changes.`;
  });
  $("review-changes").addEventListener("click",() => {
    $("confirmation-list").replaceChildren(); $("save-error").textContent="";
    for (const [id,change] of drafts) {
      const group=state.groups.find(g=>g.id===id), summary=values(group), item=el("section","confirm-item");
      item.append(el("h3","",`${group.letter} · ${summary.name}`),el("p","values",[summary.address,summary.contact].filter(Boolean).join("\n")),el("p","change",`${describe(group)} → ${describe(change)}`),el("p","values",`Applies to the ${group.fields.length} saved fields in this group. No values are edited.`));
      $("confirmation-list").append(item);
    }
    $("confirm-save").textContent=`Save ${drafts.size} ${drafts.size===1?"meaning":"meanings"} to trial`;
    $("confirm-dialog").showModal();
  });
  for (const id of ["close-dialog","back-to-edit"]) $(id).addEventListener("click",() => {if(!saving)$("confirm-dialog").close();});
  $("confirm-dialog").addEventListener("cancel",event=>{if(saving)event.preventDefault();});
  $("confirm-save").addEventListener("click",async()=>{
    if (saving || !drafts.size) return;
    saving=true; $("confirm-save").disabled=true; $("back-to-edit").disabled=true;
    try {
      const result=await api("/api/confirm",{confirmed:true,changes:[...drafts.values()]});
      state=result.state; drafts.clear(); $("confirm-dialog").close();
      $("message").textContent=`Saved ${result.saved} ${result.saved===1?"meaning":"meanings"} in your encrypted trial. Your live vault, values and defaults are unchanged.`;
    } catch(error) {$("save-error").textContent=`${error.message} If the result is unclear, reload the page to check saved progress before trying again.`;}
    finally {saving=false;$("confirm-save").disabled=false;$("back-to-edit").disabled=false;render();}
  });
  window.addEventListener("beforeunload",event=>{if(drafts.size){event.preventDefault();event.returnValue="";}});
  api("/api/state").then(result=>{state=result;render();}).catch(error=>{$("stats").textContent="Review unavailable";$("message").textContent=error.message;});
}
