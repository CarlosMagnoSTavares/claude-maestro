/* Bridge WhatsApp (Baileys) <-> Claude Maestro.
 *
 * Protocolo: JSON por linha.
 *   stdout -> {type:"qr", data}
 *             {type:"status", status:"conectando"|"conectado"|"desconectado", me}
 *             {type:"message", from, text}
 *             {type:"log", msg}
 *   stdin  <- {type:"send", to, text}
 *             {type:"logout"}
 *
 * Env: WA_SESSION_DIR (credenciais), NODE_PATH aponta para node_modules do runtime.
 */
"use strict";

function emit(obj) { process.stdout.write(JSON.stringify(obj) + "\n"); }

let baileys;
try { baileys = require("baileys"); }
catch (e1) {
  try { baileys = require("@whiskeysockets/baileys"); }
  catch (e2) { emit({ type: "log", msg: "baileys não instalado: " + e2.message }); process.exit(2); }
}
const makeWASocket = baileys.default || baileys.makeWASocket;
const { useMultiFileAuthState, DisconnectReason } = baileys;

const SESSION_DIR = process.env.WA_SESSION_DIR || "./wa_session";
let sock = null;
let stopping = false;

function jid(number) { return String(number).replace(/\D/g, "") + "@s.whatsapp.net"; }

// contas novas do WhatsApp chegam com JID oculto (@lid); mapeia telefone -> chat real
const chatByPhone = Object.create(null);

function unwrap(m) {
  // desembrulha mensagens efêmeras/viewOnce até chegar no conteúdo real
  for (let i = 0; m && i < 5; i++) {
    const inner = (m.ephemeralMessage || m.viewOnceMessage || m.viewOnceMessageV2 ||
                   m.documentWithCaptionMessage || {}).message;
    if (!inner) break;
    m = inner;
  }
  return m || {};
}

function extractText(msg) {
  const m = unwrap(msg.message || {});
  return (
    m.conversation ||
    (m.extendedTextMessage && m.extendedTextMessage.text) ||
    (m.buttonsResponseMessage && m.buttonsResponseMessage.selectedDisplayText) ||
    (m.listResponseMessage && m.listResponseMessage.title) ||
    ""
  );
}

async function resolvePhone(msg, rjid) {
  if (rjid.endsWith("@s.whatsapp.net")) return rjid.split("@")[0];
  // remoteJid veio como @lid: tenta o número real (PN) pelos campos alternativos
  const k = msg.key || {};
  const alt = k.remoteJidAlt || k.senderPn || k.participantAlt || msg.senderPn || "";
  if (alt && String(alt).includes("@s.whatsapp.net")) return String(alt).split("@")[0];
  try {
    const map = sock && sock.signalRepository && sock.signalRepository.lidMapping;
    if (map && map.getPNForLID) {
      const pn = await map.getPNForLID(rjid);
      if (pn) return String(pn).split("@")[0];
    }
  } catch (e) { /* segue com o lid como identificador */ }
  return rjid.split("@")[0];   // fallback: usa o próprio lid como id estável
}

async function start() {
  const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);
  sock = makeWASocket({
    auth: state,
    printQRInTerminal: false,
    syncFullHistory: false,
    markOnlineOnConnect: false,
    // logger silencioso (pino-compatível mínimo)
    logger: { level: "silent", child() { return this; },
      trace() {}, debug() {}, info() {}, warn() {}, error() {}, fatal() {} },
    browser: ["Claude Maestro", "Chrome", "1.0"],
  });

  sock.ev.on("creds.update", saveCreds);

  sock.ev.on("connection.update", (u) => {
    if (u.qr) emit({ type: "qr", data: u.qr });
    if (u.connection === "open") {
      const me = (sock.user && sock.user.id ? sock.user.id.split(":")[0] : "");
      emit({ type: "status", status: "conectado", me });
    }
    if (u.connection === "close") {
      const code = u.lastDisconnect && u.lastDisconnect.error &&
        u.lastDisconnect.error.output && u.lastDisconnect.error.output.statusCode;
      const loggedOut = code === DisconnectReason.loggedOut;
      emit({ type: "status", status: "desconectado", me: "" });
      if (!stopping && !loggedOut) setTimeout(start, 3000);   // reconecta sozinho
      if (loggedOut) process.exit(0);
    }
  });

  sock.ev.on("messages.upsert", async ({ messages, type }) => {
    if (type !== "notify") return;
    for (const msg of messages) {
      try {
        const rjid = msg.key && msg.key.remoteJid;
        if (!rjid || msg.key.fromMe) continue;
        // v1: só chat privado — aceita tanto @s.whatsapp.net quanto o novo @lid
        if (!rjid.endsWith("@s.whatsapp.net") && !rjid.endsWith("@lid")) continue;
        const text = extractText(msg).trim();
        if (!text) continue;
        const phone = await resolvePhone(msg, rjid);
        chatByPhone[phone] = rjid;   // resposta volta sempre para o chat de origem
        emit({ type: "message", from: phone, text });
      } catch (e) { emit({ type: "log", msg: "erro msg: " + e.message }); }
    }
  });
}

// comandos vindos do Maestro (stdin)
let buf = "";
process.stdin.on("data", async (chunk) => {
  buf += chunk.toString("utf8");
  let idx;
  while ((idx = buf.indexOf("\n")) >= 0) {
    const line = buf.slice(0, idx).trim();
    buf = buf.slice(idx + 1);
    if (!line) continue;
    let cmd;
    try { cmd = JSON.parse(line); } catch { continue; }
    try {
      if (cmd.type === "send" && sock) {
        const dest = chatByPhone[String(cmd.to).replace(/\D/g, "")] ||
                     chatByPhone[String(cmd.to)] || jid(cmd.to);
        await sock.sendMessage(dest, { text: String(cmd.text || "") });
      } else if (cmd.type === "logout" && sock) {
        stopping = true;
        await sock.logout().catch(() => {});
        process.exit(0);
      }
    } catch (e) { emit({ type: "log", msg: "erro cmd: " + e.message }); }
  }
});
process.stdin.on("end", () => process.exit(0));

emit({ type: "status", status: "conectando", me: "" });
start().catch((e) => { emit({ type: "log", msg: "falha fatal: " + e.message }); process.exit(1); });
