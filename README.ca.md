# Alberton MCP for Live

Parla amb el teu set d'Ableton Live. Un servidor [MCP](https://modelcontextprotocol.io)
més un Remote Script company permeten que un assistent d'IA llegeixi i escrigui un set
de Live per tu — crear pistes i clips, escriure i editar MIDI, governar dispositius,
dibuixar automatització, muntar arranjaments amb prompts.

> Traducció de [`README.md`](README.md), que és la versió canònica.

> **Estat** (2026-08-06): funciona. Una IA que no havia vist mai aquest projecte el va
> instal·lar des d'aquesta URL en una màquina neta i quatre minuts després feia música
> al set obert. Provat a fons en una sola configuració, de moment — vegeu *En què s'ha
> provat*.

## Instal·lació — per a músics

No et cal saber terminal. L'assistent d'IA fa la instal·lació; tu fas només tres
coses.

Necessites:

- un ordinador amb **Ableton Live 12** instal·lat — tot està verificat a macOS; res
  del disseny és específic de Mac, així que Windows hauria de funcionar, però encara
  no ho ha provat ningú
  ([explica'ns-ho](https://github.com/Alberton-projects/alberton_mcp-for-live/issues)
  si ets el primer),
- un **assistent d'IA en aquell ordinador que pugui executar ordres i connectar-se a
  servidors MCP** — Claude Desktop o Claude Code, ChatGPT Desktop amb Codex, o
  similar,
- uns **deu minuts**, amb Live obert.

Obre l'assistent i enganxa-li això:

> Vull instal·lar i fer servir això:
> https://github.com/Alberton-projects/alberton_mcp-for-live — guia'm pas a pas des
> de zero en aquest ordinador, incloent-hi connectar-te tu mateix al servidor per
> MCP. Explica cada pas en paraules planes abans de fer-lo. Quan tot estigui
> connectat, crea un clip MIDI de 4 beats amb un arpegi de Do major al set d'Ableton
> que tinc obert, perquè tots dos sapiguem que funciona.

Aquest és tot el procediment. L'assistent llegeix aquest repositori i fa la resta.
Només tres coses són teves:

1. **Un clic dins de Live**, quan t'ho demani: Preferències → Link, Tempo & MIDI →
   tria **Alberton MCP** en un slot lliure de Control Surface (Input i Output:
   **None**).
2. **Aprova** el que l'assistent proposi executar, si t'ho pregunta.
3. **Prem Cmd-S** (Ctrl-S a Windows) quan t'agradi el que sents. No es desa mai res
   per tu — el set sempre és teu, de conservar o descartar.

Quan funcioni, demana música amb les teves paraules: l'assistent veu el mateix manual
que tu pots llegir a [docs/MANUAL.ca.md](docs/MANUAL.ca.md) — què pot fer, fins on pot
arribar, i què no permet Live a ningú.

Si algun pas et confon o falla:
[obre un issue](https://github.com/Alberton-projects/alberton_mcp-for-live/issues)
i digues on t'has encallat.

## Instal·lació a mà — per a gent de terminal

Quatre passos, en aquest ordre, perquè cadascun demostra el terreny on es planta el
següent. Executa-ho tot des de l'arrel del repositori — la carpeta que t'ha donat
`git clone`.

1. **Instal·la el Remote Script i selecciona'l a Live** —
   [remote_script/Alberton_MCP/README.md](remote_script/Alberton_MCP/README.md).
2. **Comprova el pont**: `python3 tools/wire_probe.py`. No cal instal·lar res — parla
   directament amb el sòcol, així que el Python que porta macOS el fa anar. 36
   comprovacions; si no es pot connectar, et diu què mirar. No continuïs fins que
   passi.
3. **Instal·la les dependències del servidor i prova-les**:
   `uv run --directory server pytest` (149 tests, sense Ableton). Abans necessites
   `uv` — no ve amb macOS; [server/README.md](server/README.md) té la línia
   d'instal·lació.
4. **Apunta el teu client MCP al servidor** —
   [server/README.md](server/README.md) té l'entrada de Claude Desktop i una ordre
   que imprimeix els dos camins que has d'omplir.

`python3 tools/live_verify.py` és la comprovació d'extrem a extrem de les dues
meitats: 23 comprovacions contra un Live real, i tampoc no cal instal·lar res.

## En què s'ha provat

Tot el d'aquí sota s'ha verificat executant contra un Ableton real, no per deducció.
El LOM no està documentat i depèn de la versió, així que aquesta llista és l'abast
honest del que se sap que funciona — no una suposició del que probablement funcioni.

| | |
|---|---|
| **Ableton Live** | 12.4.3 **Suite** |
| **Sistema operatiu** | macOS 15 (Darwin 24.6), Apple Silicon |
| **Python dins de Live** | 3.11.6 (l'intèrpret encastat del mateix Live) |
| **Python per al servidor** | 3.10–3.13 |

**No provat enlloc:** Windows; Live 11 o anterior; Live 12.0–12.3; Live Intro i
Standard. Pot ben ser que funcioni en alguns — el disseny evita deliberadament res
d'específic de Suite, i el servidor no porta camins de plataforma escrits — però ningú
no ho ha executat, així que no s'afirma res.

Si en proves un, val la pena saber dues coses. Els Remote Scripts viuen en un altre
lloc a Windows (`%USERPROFILE%\Documents\Ableton\User Library\Remote Scripts\`), i
l'inventari del LOM contra el qual es dissenya aquest projecte es va generar a
12.4.3 — regenera'l amb `tools/introspect/` a la teva versió abans de confiar en res
d'inusual. Els informes de qualsevol dels dos desenllaços són benvinguts.

## Proves

149 tests unitaris corren sense Ableton, contra una imitació en procés del pont que
parla el protocol de cable real sobre TCP real — aquesta és la suite de CI. Vuit
sondes més corren contra una instància viva i són les que de debò han trobat els
errors: compliment del contracte, comportament de les eines d'extrem a extrem, cicle
de vida i robustesa de la connexió, cada eina del catàleg amb informe de cobertura,
crides amb la forma exacta amb què s'equivoca un model, material degenerat, els límits
declarats provocats, i mesura sota ús humà concurrent — més un informe de només
lectura del que costa llegir el teu set. Vegeu [server/README.md](server/README.md).

## Arquitectura

Dos components amb una frontera dura entremig:

- **Remote Script** (corre dins de Live). Un pont prim i genèric sobre camins
  d'objectes del LOM: llegir una propietat, escriure-la, cridar un mètode,
  llegir/escriure notes, executar un lot atòmic, subscriure's a canvis. No exposa cap
  vocabulari musical propi, així que gairebé mai no canvia — i això importa, perquè
  Live només carrega Remote Scripts en arrencar.
- **Servidor MCP** (corre fora de Live). Tota la intel·ligència: el catàleg d'eines,
  la resolució de camins, la validació, els errors estructurats, la memòria cau. Les
  capacitats noves són canvis del costat del servidor; cap reinici de Live.

Regles de disseny que no es mouran:

- El temps són sempre beats absoluts en coma flotant — mai compassos, noms de figures
  ni segons.
- Els lots són atòmics: un lot, un pas d'undo.
- El sòcol només s'enllaça a `127.0.0.1`.
- Cap telemetria.
- Errors estructurats, mai prosa interpretada com a error.

## Precedents

[`ahujasid/ableton-mcp`](https://github.com/ahujasid/ableton-mcp) (MIT, Siddharth
Ahuja, 2025) va demostrar que aquesta categoria d'eina funciona i va informar aquest
disseny. Alberton MCP for Live està escrit des de zero amb una arquitectura diferent —
un pont genèric sobre el LOM en lloc d'un vocabulari fix d'ordres al Remote Script — i
no hi comparteix codi.

## Llicència

[MIT](LICENSE).

Alberton MCP for Live no està afiliat, avalat ni patrocinat per Ableton AG. «Ableton»
i «Ableton Live» són marques d'Ableton AG.
