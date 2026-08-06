# Alberton MCP for Live — què pot fer i què no

Manual d'usuari. Tot el que hi ha aquí està implementat i provat, o marcat com a no
provat; quan una cosa és impossible, se'n diu el motiu. Escrit el 2026-08-05 contra
el contracte 1.2, Remote Script 0.3.2, 46 eines, Ableton Live 12.4.3 Suite a macOS.

> Traducció de [`MANUAL.md`](MANUAL.md), que és la versió canònica. El projecte és en
> anglès; aquesta versió existeix perquè l'usuari la va demanar.

Aquest és el mapa honest, no l'argumentari de venda. El que està marcat com a **no pot**
són coses que el Live Object Model no ofereix — cap feina en aquest servidor les canvia.

---

## Què és això

Un servidor MCP més un Remote Script company que permeten a un model de llenguatge
llegir i escriure un set d'Ableton Live: crear pistes i clips, escriure i editar MIDI,
governar paràmetres de dispositius, dibuixar automatització, muntar arranjaments i
vigilar què canvia. El model parla amb el servidor; el servidor parla amb Live a través
d'un pont petit i genèric.

Dues regles governen tota la resta:

- **El temps són sempre beats absoluts en coma flotant.** Mai compassos, mai noms de
  figures, mai segons. Un treset és `1/3` i torna exacte.
- **Cada crida que escriu és un sol pas d'undo.** Un Cmd-Z a Live desfà una crida
  sencera. Una crida que falla no deixa res a mitges.

## Abans de començar

- Live ha d'estar **obert**, amb la Control Surface `Alberton MCP` seleccionada a
  Preferències → Link, Tempo & MIDI. Sense Live, no hi ha eines.
- **Un sol client alhora.** El pont accepta una connexió. Executar una sonda de
  `tools/` desplaça el servidor MCP fins que aquest es reconnecta a la crida següent.
- El servidor treballa sobre **el set que hi hagi obert**. No guarda estat per
  document, així que carregar-ne un altre és segur — però els índexs pertanyen al set
  que hi havia obert quan els vas llegir.
- **No es desa res automàticament.** Tot va a parar al document viu; desar és cosa teva
  (l'API de Live no té cap manera de desar — vegeu *No pot*).

---

## Què fa — amb eina pròpia

### Veure el set

| Vols | Eina |
|---|---|
| Tot el set: tempo, compàs, escala, pistes, dispositius, escenes amb nom | `session_overview` |
| Una pista a fons: mescla, dispositius, **dins dels racks**, clips | `get_track` |
| Les propietats d'un clip, i opcionalment les seves notes | `get_clip` |
| Les notes d'un clip, o **estadístiques en lloc de notes** | `get_notes(summary=true)` |
| Què ha canviat des de l'última mirada | `watch`, `get_changes`, `unwatch` |

`get_track` recorre les cadenes dels racks: un rack mostra les seves cadenes, cada
cadena els seus dispositius, i cada dispositiu imbricat porta un localitzador llest per
fer servir, del tipus `"1/0/8/0/4"`. Amb `detail='full'` cada paràmetre arriba amb el
seu valor, el seu rang, la lectura en les unitats de Live, i si és esglaonat o està
desactivat.

**Demana resums abans que bolcats de notes.** Un clip de 150 notes és un mur de JSON;
el seu resum és un paràgraf — recompte, àmbit, notes per compàs, dispersió de
velocitats, polifonia màxima, i com de lluny cauen els atacs respecte de la graella (així
distingeixes una presa tocada d'una de programada).

### Escriure música

| Vols | Eina |
|---|---|
| Un clip creat, anomenat, acolorit i omplert — un sol pas d'undo | `create_clip` |
| Edició quirúrgica de notes per identificador | `edit_notes` |
| Reanomenar, recolorir, canviar loop points o compàs | `set_clip` |
| Quantitzar | `quantize_clip` |
| Copiar un clip de Session a un altre slot | `duplicate_clip_to_slot` |
| Buidar un slot de Session | `delete_clip` |
| Un clip directament a l'Arranjament | `create_arrangement_clip` |
| Copiar un clip de Session a l'Arranjament | `duplicate_clip_to_arrangement` |
| Llistar, editar o esborrar clips de l'Arranjament | `list_arrangement_clips`, `set_arrangement_clip`, `delete_arrangement_clip` |
| Importar un fitxer d'àudio | `import_audio_clip` |
| Estructura que un humà pugui veure | `create_reference_clip` |

Les notes porten altura, inici, durada, velocitat, mute, probabilitat, desviació de
velocitat i velocitat de deixada. Cada nota té un **identificador estable** — això és el
que fa que editar sigui quirúrgic i no destruir-i-reescriure.

### Pistes, mescla, escenes, transport

`create_midi_track`, `create_audio_track`, `set_track` (nom, color, arm, mute, solo,
volum en **dB o normalitzat**, pan, sends), `delete_track`, `duplicate_track`;
`create_scene`, `set_scene`, `delete_scene`; `fire_clip`, `fire_scene`, `stop_clip`,
`stop_all_clips`; `set_song` (tempo, compàs, escala, tònica, groove, metrònom);
`transport` (play/stop/continue i el capçal); `show_view`.

### Dispositius i automatització

| Vols | Eina |
|---|---|
| Trobar alguna cosa carregable | `browse` |
| Carregar-la | `load_device` |
| Rellegir el navegador després d'instal·lar un pack | `refresh_browser_index` |
| Posar un paràmetre — inclosos macros de rack i dispositius dins de racks | `set_device_parameter` |
| Dibuixar automatització a partir d'uns quants punts | `automate_parameter` |
| Esborrar automatització | `clear_automation` |

`set_device_parameter` accepta un valor dins del rang del paràmetre, o
`{"display": -6.0}` per escriure en les unitats que Live mostra (dB, %, semitons).
`automate_parameter` accepta la **forma** — uns quants punts de trencament — i el
servidor la renderitza dins de l'envolupant.

### Fer diverses coses com un sol pas d'undo

`song_batch` compila una seqüència d'eines en un únic lot atòmic: o hi cau tot o no hi
cau res, i un Cmd-Z ho desfà tot plegat.

### Arribar a qualsevol altra cosa

`lom_get`, `lom_set`, `lom_call` i `lom_describe` exposen el Live Object Model
directament. Qualsevol cosa que ofereixi l'API de Live és abastable per aquí el dia que
la necessitis — la secció següent diu què et dona això avui.

---

## Què pot fer per l'escotilla — encara sense eina pròpia

Això funciona avui amb `lom_call` / `lom_set`. **Cada fila d'aquí sota s'ha executat
contra un Live real i s'ha observat que funciona** — no hi ha res que hi consti només
per la documentació.

| Vols | Com |
|---|---|
| Esborrar un dispositiu | `lom_call` sobre la pista: `delete_device(índex)` |
| Moure o reordenar dispositius | `lom_call` sobre song: `move_device(dispositiu, pista, posició)` — vegeu l'advertiment de sota |
| Reanomenar un dispositiu | `lom_set` del seu `name` |
| Crear una pista de retorn | `lom_call` sobre song: `create_return_track()` — cada pista hi guanya un send |
| Duplicar una escena | `lom_call` sobre song: `duplicate_scene(índex)` |
| Locators (marcadors) d'Arranjament | `set_or_delete_cue` al capçal; `CuePoint.name` s'escriu; `jump()` hi porta |
| Routing d'entrada/sortida de pista | llegir `available_input_routing_types`, escriure `input_routing_type` amb l'`$obj` d'aquell element |
| Gravar clips de Session a l'Arranjament | posar `record_mode`, disparar el clip |
| Loop brace, punch in/out, overdub | `lom_set` sobre song |
| Moure warp markers d'un clip d'àudio | `move_warp_marker(beat, distància)` |
| Reescriure un sistema d'afinació | `lom_set` de `note_tunings` (cents absoluts per grau) — verificat amb 72-EDO |
| Retallar un clip, duplicar-ne el loop o una regió | `crop()`, `duplicate_loop()`, `duplicate_region(...)` |
| Capturar el MIDI acabat de tocar | `capture_midi()` — no fa res, i no es queixa, si no hi ha res a capturar |
| Tap tempo, empènyer, escrubar | `tap_tempo()`, `jump_by(...)`, `scrub_by(...)` |
| Llegir take lanes (comping) | `take_lanes` d'una pista — només lectura |

**Dos advertiments que mereixen paràgraf propi.** `move_device` retorna l'índex on ha
acabat el dispositiu, i **Live fa complir les regles d'ordre de la cadena**: en una
pista MIDI, un efecte d'àudio no es pot posar davant de l'instrument. Un moviment
il·legal no dona error — retorna l'índex sense canviar i no passa res, així que compara
abans i després (o pregunta-ho abans amb `find_device_position`). I `jump_by` es mou
relatiu a `song.start_time`, que és on començaria la reproducció i **no sempre és el
capçal visible**: se l'ha vist aterrar al beat 4 amb el capçal a 39,8. Quan vulguis un
moviment absolut, fes servir l'eina `transport` amb una posició.

**La microtonalitat és possible.** Amb un fitxer d'afinació activat a mà dins de Live,
tota l'escala es llegeix i s'escriu: `note_tunings` és una llista plana de cents
absoluts per grau. Verificat contra un 72-EDO — un grau doblegat 5 cents, la resta
intactes, i després restaurat bit a bit. L'API de Live no pot *activar* una afinació;
la carrega un humà, i a partir d'aquí tot és governable.

---

## Què no pot fer

Cadascuna d'aquestes coses és una propietat de l'API de Live, verificada. No es
resolen treballant en aquest servidor.

**El contingut de Max for Live és invisible.** Una graella `live.step`, un multislider
— qualsevol cosa que un autor de M4L declari com a llista o blob — no apareix a la
llista de paràmetres del dispositiu. Un step sequencer respon amb els seus vint
botons i **ni una sola de les seves notes**, i res de la resposta diu que hi falti
contingut. `get_track` ara marca els dispositius Max for Live i avisa que les seves
llistes de paràmetres poden ser incompletes, que és el més honest possible: l'API no
ofereix cap manera de comptar el que amaga.

**Congelar, descongelar, agrupar, desagrupar.** Només lectura al LOM i sense cap mètode
per canviar-ho. Ho fa un humà dins de Live. (Pitjor: una pista congelada *accepta*
escriptures de notes per l'API tot i que la UI de Live les bloqueja — les notes hi
cauen, però l'àudio renderitzat no canvia. `edit_notes` avisa quan ho detecta.)

**Moure un clip de l'Arranjament.** `start_time` i `end_time` no tenen setter. Moure
vol dir esborrar i recrear, o duplicar a la nova posició.

**Les follow actions i les variacions de macros de rack no existeixen** al LOM de Live
12.4.3. Dues funcions que sonen òbvies i que simplement no hi són.

**Afegir un warp marker.** Live vol un objecte C++ `TWarpMarker` i cap conversor
accepta res que JSON pugui enviar. Els marcadors existents sí que es poden moure i
esborrar.

**Construir esdeveniments d'envolupant directament.** La mateixa família. El servidor
ho esquiva renderitzant les formes com un enrajolat de passos petits — així obtens la
forma que has demanat, feta de passos.

**Canviar el diapasó (La = 440).** `ReferencePitch.frequency` no té setter. El diapasó
és el que digui el fitxer d'afinació.

**Exportar, renderitzar o fer bounce d'àudio.** No hi ha absolutament res al LOM.

**Desar el set.** No existeix cap mètode de desat a l'API de Live. Tot el que
s'escriu va al document obert; conservar-ho és un humà prement Cmd-S.

**Tocar les preferències de Live, instal·lar Remote Scripts o reiniciar Live.**

**Moure el capçal més enllà del final de la cançó.** Live refusa posicions més enllà de
`song_length`.

---

## Sorpreses que val la pena saber

Cadascuna li ha costat un vespre a algú.

- **Els colors s'ajusten a la paleta de Live.** Demanes `#FF8800` i obtens `#F66C03`.
  El read-back és la veritat.
- **El tempo es quantitza a float32.** Escrius 123.45, llegeixes 123.44999694824219.
- **Algunes propietats s'apliquen un tick tard.** `record_mode`, `loop`, `punch_in`,
  `punch_out`: el read-back de la mateixa crida encara mostra el valor vell. Torna a
  llegir un moment després. (`loop_start`, `loop_length` i `arrangement_overdub` són
  immediats.)
- **Live reporta el nom CURT dels paràmetres.** El dispositiu diu `PC Interval`, l'API
  diu `PC ms`; `Cymbals` és `Cymb`. Fes servir el nom que et donen les eines.
- **Disparar un clip engega el transport, i `stop_clip` no l'atura.** Fes servir
  `transport(action='stop')`.
- **Carregar un segon instrument substitueix el primer** en aquella pista. Els efectes
  s'hi afegeixen.
- **`is_quantized` vol dir "té passos amb nom", no "només enters".** Si un valor ha
  sobreviscut ho diu el read-back, mai la bandera.
- **Els noms sobreviuen intactes** — cometes, salts de línia, emoji, CJK, 300
  caràcters. Byte a byte.
- **Anomenar una pista és més segur que indexar-la.** Un nom es resol *i es protegeix*:
  si l'objecte s'ha mogut o s'ha esborrat entre la consulta i l'escriptura, la crida
  refusa i no escriu res. Un índex pelat vol dir "el que hi hagi en aquest índex, ara".
- **`create_arrangement_clip` i `import_audio_clip` són dos passos d'undo**, no un: el
  clip ha d'existir abans de poder ser anomenat i omplert.

---

## Límits i velocitat

| | |
|---|---|
| Anada i tornada a Live | ~0,2–0,4 s, **carregui el que carregui** |
| Operacions per lot | 256 |
| Notes per lectura o escriptura | 20 000 |
| Mida de missatge | 16 MiB |
| Vigilàncies actives | 128 |
| Cua d'esdeveniments | 4 096, i després un avís de desbordament |

Com que una anada i tornada costa el mateix carregui el que carregui, **el que costa
temps és el nombre de crides, no la seva mida**. Demanar una cosa gran guanya a
demanar-ne deu de petites.

Dues respostes s'ajusten a la mida del set en lloc de bolcar-ho tot: `session_overview`
omet el mapa de clips per slot per damunt de 600 slots (i ho diu), i
`get_track(detail='full')` es limita als noms dels paràmetres per damunt de 400
paràmetres en una pista.

---

## Quan alguna cosa va malament

Els errors arriben com `{"error": {"code", "message", "hint"}}` — mai com a prosa que
calgui interpretar. Els codis que importen:

- **`bridge_unreachable`** — Live està tancat, o la Control Surface no està
  seleccionada, o un altre client té el sòcol.
- **`not_found`** — inclòs *"s'ha mogut o s'ha esborrat abans que aquesta crida
  arribés a Live"*, que vol dir que **no s'ha escrit res**. Torna a llegir el set i
  reintenta.
- **`conflict`** — l'slot o l'espai ja està ocupat.
- **`invalid_argument`** — el hint llista els valors legals.

Si Live deixa de respondre a tot: recarrega la Control Surface (posa-la a None i
torna-la a posar). Això reinicia el pont sense reiniciar Live.

---

*Aquest manual descriu comportament verificat a Live 12.4.3 Suite, macOS, Apple
Silicon. No s'ha provat res a Windows ni en altres versions de Live. El raonament i les
evidències són a `docs/HANDOFF.md`; l'especificació del protocol i de les eines, a
`docs/CONTRACT.md`.*
