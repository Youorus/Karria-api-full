"""La console web de l'équipe : une page, sans dépendance, protégée par le jeton.

Pourquoi une page servie par l'API
----------------------------------
L'équipe, c'est Marc : il lui faut un endroit pour lire et répondre sans
installer quoi que ce soit. Une page HTML que l'API sert elle-même, qui
demande le jeton d'équipe une fois (gardé dans `sessionStorage`, jamais
dans l'URL) et parle aux routes JSON `/internal/support/*` avec
`Authorization: Bearer`. Sobre : une colonne de fils, un fil ouvert, une
zone de réponse, une image à joindre. Rafraîchie toutes les huit secondes.

La page ne sait rien : sans jeton valide, chaque appel répond 401 et elle
redemande le jeton. Elle n'est pas dans le schéma OpenAPI.
"""

from __future__ import annotations

from typing import Final

CONSOLE_PAGE: Final = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Karria — Parler à l'équipe</title>
<style>
  :root { color-scheme: light dark; --bleu:#1877f2; --gris:#6b7280; --fond:#f6f7f9; --carte:#fff; --bord:#e5e7eb; --texte:#111827; }
  @media (prefers-color-scheme: dark) { :root { --fond:#0f1115; --carte:#171a21; --bord:#262a33; --texte:#e5e7eb; --gris:#9ca3af; } }
  * { box-sizing: border-box; }
  body { margin:0; font: 15px/1.5 -apple-system, "Segoe UI", Roboto, sans-serif; background:var(--fond); color:var(--texte); }
  header { padding:14px 20px; border-bottom:1px solid var(--bord); display:flex; justify-content:space-between; align-items:center; }
  header h1 { font-size:17px; margin:0; font-weight:700; }
  header button { background:none; border:1px solid var(--bord); color:var(--gris); border-radius:999px; padding:4px 12px; cursor:pointer; }
  main { display:grid; grid-template-columns: 320px 1fr; height: calc(100vh - 57px); }
  @media (max-width: 760px) { main { grid-template-columns: 1fr; } #fil { display:none; } body.ouvert #liste { display:none; } body.ouvert #fil { display:flex; } }
  #liste { border-right:1px solid var(--bord); overflow:auto; }
  #liste .filtre { padding:10px 16px; border-bottom:1px solid var(--bord); color:var(--gris); display:flex; gap:12px; align-items:center; }
  .ligne { padding:12px 16px; border-bottom:1px solid var(--bord); cursor:pointer; }
  .ligne:hover, .ligne.actif { background: color-mix(in srgb, var(--bleu) 8%, var(--carte)); }
  .ligne .qui { font-weight:600; display:flex; justify-content:space-between; gap:8px; }
  .ligne .quand { color:var(--gris); font-size:13px; font-weight:400; white-space:nowrap; }
  .ligne .apercu { color:var(--gris); font-size:14px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .ligne.attend .qui::before { content:""; display:inline-block; width:8px; height:8px; border-radius:50%; background:var(--bleu); margin-right:8px; vertical-align:middle; }
  #fil { display:flex; flex-direction:column; min-height:0; }
  #entete { padding:12px 20px; border-bottom:1px solid var(--bord); display:flex; align-items:center; gap:12px; }
  #entete .retour { display:none; } @media (max-width:760px) { #entete .retour { display:inline; } }
  #messages { flex:1; overflow:auto; padding:20px; display:flex; flex-direction:column; gap:10px; }
  .msg { max-width:70%; padding:10px 14px; border-radius:16px; background:var(--carte); border:1px solid var(--bord); white-space:pre-wrap; word-break:break-word; }
  .msg.team { align-self:flex-end; background:var(--bleu); color:#fff; border-color:var(--bleu); }
  .msg .meta { display:block; font-size:12px; opacity:.7; margin-top:4px; }
  .msg img { max-width:100%; border-radius:10px; display:block; }
  .msg audio { display:block; max-width:100%; }
  #reponse { border-top:1px solid var(--bord); padding:12px 20px; display:flex; gap:10px; align-items:flex-end; }
  #reponse textarea { flex:1; resize:none; min-height:44px; max-height:160px; padding:10px 12px; border-radius:12px; border:1px solid var(--bord); background:var(--carte); color:var(--texte); font:inherit; }
  #reponse button { height:44px; padding:0 18px; border-radius:999px; border:0; background:var(--bleu); color:#fff; font-weight:600; cursor:pointer; }
  #reponse button:disabled { opacity:.5; cursor:default; }
  #reponse label { height:44px; width:44px; display:grid; place-items:center; border-radius:999px; border:1px solid var(--bord); cursor:pointer; color:var(--gris); }
  #reponse input[type=file] { display:none; }
  .vide { color:var(--gris); text-align:center; padding:60px 20px; }
  #porte { position:fixed; inset:0; background:var(--fond); display:grid; place-items:center; }
  #porte form { background:var(--carte); border:1px solid var(--bord); border-radius:20px; padding:28px; width:min(380px, 90vw); display:flex; flex-direction:column; gap:12px; }
  #porte input { padding:12px; border-radius:12px; border:1px solid var(--bord); background:var(--fond); color:var(--texte); font:inherit; }
  #porte button { padding:12px; border-radius:999px; border:0; background:var(--bleu); color:#fff; font-weight:600; cursor:pointer; }
  #porte p { margin:0; color:var(--gris); font-size:14px; }
  #etat { color:var(--gris); font-size:13px; }
  .joint { font-size:13px; color:var(--gris); }
</style>
</head>
<body>
<header>
  <h1>Parler à l'équipe — console Karria</h1>
  <div><span id="etat"></span> <button id="sortir" type="button">Changer de jeton</button></div>
</header>
<main>
  <section id="liste">
    <div class="filtre">
      <label><input type="checkbox" id="attente" checked> Seulement ceux qui attendent une réponse</label>
    </div>
    <div id="lignes"></div>
  </section>
  <section id="fil">
    <div id="entete"><a class="retour" href="#" id="retour">&larr; Fils</a><strong id="qui">Choisis un fil</strong><span id="lu" class="joint"></span></div>
    <div id="messages"><div class="vide">Un fil ouvert s'affiche ici, et la personne verra ta réponse en direct sur son téléphone.</div></div>
    <form id="reponse" hidden>
      <label title="Joindre une image"><input type="file" id="image" accept="image/jpeg,image/png,image/webp">📎</label>
      <textarea id="texte" placeholder="Réponds ici — c'est toi, pas Karria." rows="1"></textarea>
      <button type="submit" id="envoyer">Envoyer</button>
    </form>
  </section>
</main>
<div id="porte" hidden>
  <form id="formePorte">
    <strong>Jeton d'équipe</strong>
    <p>Le jeton <code>SUPPORT__ADMIN_TOKEN</code> de l'API. Il reste dans cet onglet, jamais dans l'adresse.</p>
    <input type="password" id="jeton" autocomplete="off" placeholder="Jeton">
    <button type="submit">Entrer</button>
  </form>
</div>
<script>
(function () {
  "use strict";
  var BASE = location.pathname.replace(/\\/console\\/?$/, "");
  var $ = function (id) { return document.getElementById(id); };
  var jeton = sessionStorage.getItem("karria_support_token") || "";
  var ouvert = location.hash.slice(1) || null;
  var minuterie = null;

  function etat(texte) { $("etat").textContent = texte; }
  function quand(iso) {
    if (!iso) return "";
    var d = new Date(iso), maintenant = new Date();
    var memeJour = d.toDateString() === maintenant.toDateString();
    return memeJour ? d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })
                    : d.toLocaleDateString("fr-FR", { day: "numeric", month: "short" }) + " " + d.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  }
  function demanderJeton() { $("porte").hidden = false; $("jeton").focus(); }
  function appeler(chemin, options) {
    options = options || {};
    options.headers = Object.assign({ "Authorization": "Bearer " + jeton }, options.headers || {});
    return fetch(BASE + chemin, options).then(function (r) {
      if (r.status === 401) { demanderJeton(); throw new Error("jeton"); }
      if (!r.ok) { return r.json().catch(function(){ return {}; }).then(function (c) { throw new Error((c.error && c.error.message) || ("Erreur " + r.status)); }); }
      return r.status === 204 ? null : r.json();
    });
  }
  function texteSur(node, texte) { node.textContent = texte; return node; }
  function rendreListe(page) {
    var conteneur = $("lignes"); conteneur.innerHTML = "";
    if (!page.items.length) { conteneur.appendChild(texteSur(Object.assign(document.createElement("div"), { className: "vide" }), "Personne n'attend. Tout est répondu.")); return; }
    page.items.forEach(function (c) {
      var ligne = document.createElement("div");
      ligne.className = "ligne" + (c.awaits_team ? " attend" : "") + (c.id === ouvert ? " actif" : "");
      var qui = document.createElement("div"); qui.className = "qui";
      qui.appendChild(texteSur(document.createElement("span"), c.user_email || "compte inconnu"));
      qui.appendChild(texteSur(Object.assign(document.createElement("span"), { className: "quand" }), quand(c.updated_at)));
      var apercu = texteSur(document.createElement("div"), (c.last_message_kind === "voice" ? "🎙 " : c.last_message_kind === "image" ? "🖼 " : "") + (c.last_message_preview || "—"));
      apercu.className = "apercu";
      ligne.appendChild(qui); ligne.appendChild(apercu);
      ligne.addEventListener("click", function () { ouvrir(c.id); });
      conteneur.appendChild(ligne);
    });
  }
  function rendreFil(fil) {
    $("qui").textContent = fil.user_email || "compte inconnu";
    $("lu").textContent = fil.user_read_at ? "lu par la personne " + quand(fil.user_read_at) : "";
    $("reponse").hidden = false;
    var zone = $("messages"); zone.innerHTML = "";
    if (!fil.messages.length) zone.appendChild(texteSur(Object.assign(document.createElement("div"), { className: "vide" }), "Pas encore de message."));
    fil.messages.forEach(function (m) {
      var bloc = document.createElement("div"); bloc.className = "msg " + m.author;
      if (m.kind === "text") bloc.appendChild(document.createTextNode(m.body || ""));
      else if (m.kind === "image") { var img = document.createElement("img"); img.src = m.attachment.url; img.alt = "Image envoyée"; bloc.appendChild(img); }
      else if (m.kind === "voice") { var audio = document.createElement("audio"); audio.controls = true; audio.src = m.attachment.url; bloc.appendChild(audio); }
      bloc.appendChild(texteSur(Object.assign(document.createElement("span"), { className: "meta" }), (m.author === "team" ? "Équipe · " : "") + quand(m.created_at)));
      zone.appendChild(bloc);
    });
    zone.scrollTop = zone.scrollHeight;
  }
  function chargerListe() {
    return appeler("/conversations?limit=100&awaiting_only=" + ($("attente").checked ? "true" : "false")).then(rendreListe);
  }
  function chargerFil() {
    if (!ouvert) return Promise.resolve();
    return appeler("/conversations/" + ouvert).then(rendreFil);
  }
  function rafraichir() {
    etat("…");
    return Promise.all([chargerListe(), chargerFil()]).then(function () { etat("à jour " + quand(new Date().toISOString())); })
      .catch(function (e) { if (e.message !== "jeton") etat(e.message); });
  }
  function ouvrir(id) {
    ouvert = id; location.hash = id; document.body.classList.add("ouvert");
    rafraichir();
  }
  function planifier() { clearInterval(minuterie); minuterie = setInterval(rafraichir, 8000); }

  $("formePorte").addEventListener("submit", function (e) {
    e.preventDefault();
    jeton = $("jeton").value.trim(); sessionStorage.setItem("karria_support_token", jeton);
    $("porte").hidden = true; $("jeton").value = ""; rafraichir(); planifier();
  });
  $("sortir").addEventListener("click", function () { sessionStorage.removeItem("karria_support_token"); jeton = ""; demanderJeton(); });
  $("attente").addEventListener("change", chargerListe);
  $("retour").addEventListener("click", function (e) { e.preventDefault(); document.body.classList.remove("ouvert"); });
  $("texte").addEventListener("keydown", function (e) { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); $("reponse").requestSubmit(); } });
  $("reponse").addEventListener("submit", function (e) {
    e.preventDefault();
    if (!ouvert) return;
    var corps = $("texte").value.trim(); if (!corps) return;
    $("envoyer").disabled = true;
    appeler("/conversations/" + ouvert + "/messages", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ body: corps }) })
      .then(function () { $("texte").value = ""; return rafraichir(); })
      .catch(function (e2) { etat(e2.message); })
      .then(function () { $("envoyer").disabled = false; $("texte").focus(); });
  });
  $("image").addEventListener("change", function () {
    var fichier = $("image").files[0]; if (!fichier || !ouvert) return;
    var forme = new FormData(); forme.append("file", fichier, fichier.name);
    etat("envoi de l'image…");
    appeler("/conversations/" + ouvert + "/messages/media", { method: "POST", body: forme })
      .then(rafraichir).catch(function (e) { etat(e.message); }).then(function () { $("image").value = ""; });
  });

  if (!jeton) demanderJeton(); else { if (ouvert) document.body.classList.add("ouvert"); rafraichir(); planifier(); }
})();
</script>
</body>
</html>
"""
