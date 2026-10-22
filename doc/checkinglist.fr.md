# Liste des vérifications MapChecker

[中文](checkinglist.md) | Français

Ce document liste tout ce que `check_lanelet_map.py` vérifie actuellement, et les conditions dans lesquelles chaque WARNING / ERROR est émis. Il est établi d'après le code au 2026-10-21 ; les « valeurs actuelles » citées proviennent de `config/config.yaml` et de `ext/<nom>/config/config.yaml`. Après modification de la configuration, c'est le fichier de configuration qui fait foi.

## Vue d'ensemble

| # | Vérification (nom dans le Summary et la liste d'ignorés) | Objet vérifié | Quand elle s'exécute | Niveau |
|---|---|---|---|---|
| 1 | `turn_direction` | Lanelets véhicule | Toujours (sauf avec `--only-ext`) | WARNING |
| 2 | `right_of_way` | Paires de lanelets véhicule qui se chevauchent, éléments de régulation `right_of_way` | Toujours (sauf avec `--only-ext`) | WARNING / ERROR |
| 3 | `autoware` | Tout le fichier de carte | `autoware_checking: true` (actuellement `false`), nécessite de sourcer ROS | WARNING / ERROR |
| Extension | `altitude` | Altitude `ele` des nœuds des bordures des lanelets véhicule | `enable: true` (actuellement activée) | WARNING |
| Extension | `crosswalk` | Lanelets `subtype=crosswalk` | `enable: true` (actuellement activée) | WARNING |
| Extension | `_template` | Tags des lanelets véhicule | Jamais automatiquement, seulement avec `--ext _template` | WARNING |

## Règles générales

- **Lanelet véhicule** : lanelet dont le `subtype` est `road`, `highway` ou `road_shoulder`. Les vérifications 1, 2 et altitude ne portent que sur les lanelets véhicule.
- **Niveau** : `[WARNING]` demande une confirmation manuelle ; `[ERROR]` est une erreur certaine.
- **Code de sortie** : `0` aucun problème ; `1` au moins un WARNING / ERROR non ignoré ; `2` erreur d'option, de configuration ou d'environnement.
- **Liste d'ignorés** (`config/ignore.yaml`, ainsi que `ignore` dans la configuration de chaque extension) :
  - Les lanelets sous `all` sont ignorées par toutes les vérifications ; les autres clés ne s'appliquent qu'à la vérification correspondante.
  - Quand un problème concerne plusieurs lanelets (par exemple une paire de lanelets dans la vérification 2, ou une bordure partagée par deux voies adjacentes dans altitude), il suffit que l'une d'elles soit ignorée pour que le problème ne soit pas affiché.
  - Les problèmes qui ne concernent pas une lanelet précise (nœud sans `ele`, erreur de format / d'analyse de la carte) ne peuvent pas être ignorés.
- **AOI** (`only_check_AOI: true`, actuellement `false`) :
  - Seuls sont signalés les problèmes dont toutes les lanelets concernées sont entièrement situées dans une AOI (way fermé `subtype=AOI`).
  - Les relations géométriques (chevauchements, prédécesseurs / successeurs) restent calculées sur toute la carte.
  - Si la carte ne contient aucune AOI, aucune vérification n'est effectuée et le programme se termine (code de sortie `0`).
- **Simples informations, non comptées comme problèmes** :
  - Lanelets impossibles à analyser (bordure gauche / droite manquante, way de bordure inexistant, référence à un nœud inexistant) : listées au début du rapport dans `Lanelets not loaded (incomplete, not checked)`, elles ne participent à aucune vérification.
  - Passages piétons qui ne chevauchent aucune voie : seuls leurs id sont listés.
  - Le score `p` de `Using_LR`, les Hotspots et la carte de chaleur : ils ne font que noter, trier et compter les problèmes déjà affichés, sans en ajouter ni en masquer aucun.

## Notions de base (communes aux vérifications 1 et 2)

| Notion | Critère |
|---|---|
| Virage | Sur un tronçon quelconque de longueur au plus `turn_window` (actuellement 30 m), la variation de cap de la ligne centrale (moyenne des bordures gauche et droite) est ≥ `turn_angle` (actuellement 30°). Le cap est d'abord lissé par une moyenne glissante d'environ 2 m, pour éliminer le bruit des angles aux nœuds ; pour une lanelet plus courte que `turn_window`, c'est l'angle sur toute sa longueur. Positif à gauche, négatif à droite |
| Ligne droite | Tout ce qui n'est pas un virage |
| Chevauchement | La surface de chevauchement de deux lanelets véhicule est ≥ `min_overlap_area` (actuellement 1.0 m², estimée par échantillonnage sur une grille de 0.5 m). Deux lanelets toutes deux droites, ou de sens opposés (écart de cap > 150° au chevauchement), qui se chevauchent sur une longueur ≥ `parallel_min_length` (actuellement 10 m) et dont les lignes centrales restent à ≥ `parallel_min_gap` (actuellement 1.5 m) l'une de l'autre le long du chevauchement sont des voies parallèles (par exemple sur une route étroite ou qui se rétrécit) et ne comptent pas comme chevauchement. Deux lanelets qui se suivent (l'une est le prédécesseur / successeur de l'autre) ou qui sont adjacentes (way de bordure partagé) ne comptent pas comme chevauchement |
| Divergence (diverge) | Les deux lanelets qui se chevauchent ont un prédécesseur commun, ou le même point de départ |
| Convergence (merge) | Les deux lanelets qui se chevauchent ont un successeur commun, ou le même point d'arrivée |
| Croisement (crossing) | Chevauchement qui n'est ni une divergence ni une convergence |
| Intersecter | Croiser une autre lanelet ou converger avec elle ; une divergence ne compte pas |

## Vérification 1 : `turn_direction`

Objet vérifié : lanelets véhicule ; lorsque la vérification 3 est activée, également les lanelets signalées par Autoware (même si leur `subtype` n'est pas un type véhicule).

La configuration actuelle est `straight_as_turn_light: true`, `ignore_isolated_turns: true` ; les problèmes suivants sont alors signalés :

| Problème | Condition | Message |
|---|---|---|
| Valeur invalide | `turn_direction` présent, mais sa valeur n'est pas `left` / `right` / `straight`. Cette lanelet ne passe pas les vérifications suivantes | `turn_direction="..." is not a valid value (left/right/straight)` |
| Virage sans clignotant | Virage sans `turn_direction` | `curve without turn_direction (geometry turns right, suggest turn_direction=right)` |
| Lanelet droite qui intersecte sans `turn_direction` | Ligne droite, intersecte, sans `turn_direction` | `intersects other lanes without turn_direction (geometry straight, suggest turn_direction=straight)` |
| Détecté uniquement par Autoware | Vérification 3 activée ; en conflit avec d'autres lanelets / areas dans le graphe de routage Autoware, sans `turn_direction`, et non signalée par les vérifications géométriques ci-dessus (par exemple divergence, chevauchement inférieur à `min_overlap_area`, voies parallèles, virage ignoré à cause de `ignore_isolated_turns`) | `conflicts with <id> in Autoware routing graph without turn_direction` |

Avec la configuration actuelle, **ne sont pas signalés** :

- Les virages qui ne croisent aucune autre lanelet et ne convergent avec aucune, par exemple un virage qui ne fait que diverger (`ignore_isolated_turns: true`).
- Les virages dont la direction ne correspond pas à la géométrie : toute valeur valide est acceptée, la direction n'est pas vérifiée (`straight_as_turn_light: true`).
- Les lanelets en ligne droite qui n'intersectent aucune autre lanelet.

Avec `straight_as_turn_light: false`, la direction est vérifiée et les deux lignes du milieu du tableau ci-dessus sont remplacées par :

| Problème | Condition | Message |
|---|---|---|
| Virage sans clignotant | Virage sans `turn_direction` | `curve without turn_direction (...)` |
| Virage marqué straight | Virage, `turn_direction=straight` | `curve tagged turn_direction=straight, no turn signal (geometry turns left)` |
| Direction inversée | Virage dont le `turn_direction` est opposé au sens géométrique | `turn_direction=left is opposite to geometry (turns right)` |
| Ligne droite marquée gauche / droite | Ligne droite, intersecte, `turn_direction` vaut `left` / `right` | `intersects other lanes, turn_direction=left but geometry is straight` |

Les lanelets droites qui intersectent n'ont alors pas à être marquées `straight`. Avec `ignore_isolated_turns: false`, les virages qui n'intersectent rien sont aussi vérifiés.

La fin de chaque ligne `| middle (x, y), angle +45°, length 12.3 m` donne le milieu de la ligne centrale, la variation de cap et la longueur.

## Vérification 2 : `right_of_way`

### 2a. Problèmes des éléments de régulation `right_of_way` existants

Pour chaque élément de régulation `subtype=right_of_way` de la carte :

| Niveau | Condition | Message |
|---|---|---|
| WARNING | Une lanelet référencée (membre `right_of_way` ou `yield`) n'existe pas dans la carte | `right_of_way <id>: referenced lanelet <id> does not exist` |
| WARNING | Membre `right_of_way` ou membre `yield` manquant | `right_of_way <id>: missing right_of_way or yield member` |
| WARNING | La lanelet prioritaire (membre `right_of_way`) ne référence pas elle-même cet élément de régulation ; Autoware ignore alors la règle | `right_of_way <id>: priority lanelet <id> does not reference this regulatory element (ignored by Autoware)` |
| ERROR | La lanelet prioritaire et la lanelet qui cède le passage ne se chevauchent pas du tout (confirmé sur une grille fine de 0.1 m) ; la distance minimale entre les deux est indiquée | `right_of_way <id>: lanelet <r> (right_of_way) and <y> (yield) do not overlap at all (min distance 3.2 m)` |

### 2b. `right_of_way` manquant

| Niveau | Condition | Message |
|---|---|---|
| WARNING | Deux lanelets véhicule se croisent ou convergent, et aucun élément de régulation `right_of_way` ne liste l'une comme `right_of_way` et l'autre comme `yield` (peu importe laquelle est prioritaire) | `lanelet <a> / <b>: merge overlap (~13.5 m² at (x, y)) without right_of_way`, ou `crossing overlap ...` pour un croisement |

- Si les deux lanelets référencent un feu de circulation, le problème est quand même signalé, avec seulement `[both controlled by traffic lights]` ajouté à la fin.
- Les alertes sont groupées par zone (`-- Area k`) : les lanelets reliées par des chevauchements forment une zone, en général une intersection.
- **Ne sont pas signalés** : les paires de lanelets qui divergent, les lanelets qui se suivent ou qui sont adjacentes, les chevauchements inférieurs à `min_overlap_area`, et les voies parallèles (deux lanelets droites ou de sens opposés qui se chevauchent sur ≥ `parallel_min_length` avec des lignes centrales toujours à ≥ `parallel_min_gap` l'une de l'autre).

## Vérification 3 : `autoware` (avec `autoware_checking: true`)

Exécute `utils/validate_autoware_map.py`, version Python de `autoware_lanelet2_validation` : projection MGRS et graphe de routage construit avec les règles de circulation Germany / Vehicle.

| Niveau | Problème | Condition |
|---|---|---|
| ERROR | `format_version` invalide | Pas de `format_version` dans `MetaInfo` ; ce n'est pas un numéro de version comme `2`, `2.1`, `2.1.0` ; ou la version majeure est supérieure à 2 |
| ERROR | Erreur d'analyse de la carte | Chaque erreur signalée par Lanelet2 à la lecture de la carte (`parse error: ...`), ou échec direct de la lecture (`failed to load map: ...`). En cas d'erreur d'analyse, la vérification `turn_direction` ci-dessous est sautée |
| WARNING | Nœuds sans altitude | Des `<node>` du fichier de carte n'ont pas de tag `ele` (pas seulement les nœuds des lanelets). Regroupé en une seule alerte listant les 10 premiers id de nœud |
| WARNING | Feu sans light_bulb | L'élément de régulation feu de circulation référencé par une lanelet n'a pas de membre `light_bulbs` |
| WARNING | light_bulb sans id | Une linestring de `light_bulbs` n'a pas de tag `traffic_light_id` |
| WARNING | Feu en polygone | Le feu (membre `refers`) est un polygone ; seules les linestrings sont prises en charge |
| WARNING | Feu sans hauteur | Le feu (membre `refers`) n'a pas de tag `height` |
| WARNING (fusionné dans la vérification 1) | Lanelet en conflit sans `turn_direction` | Lanelet praticable par les véhicules, en conflit avec d'autres lanelets / areas dans le graphe de routage, et sans tag `turn_direction`. Les lanelets déjà signalées par la vérification géométrique ne sont pas signalées à nouveau |

- Le format, les erreurs d'analyse et l'altitude des nœuds sont des problèmes de toute la carte ; ils ne dépendent ni de l'AOI ni de la liste d'ignorés.
- Les problèmes de feux ne sont signalés que si la lanelet qui les référence est dans l'AOI, et peuvent être ignorés avec `autoware:` dans `config/ignore.yaml`.

## Extension `altitude`

Objet vérifié : les nœuds des bordures gauche et droite des lanelets véhicule ; une bordure partagée par deux voies adjacentes n'est vérifiée qu'une fois. Pente = dénivelé / distance horizontale.

Valeurs actuelles : `max_slope: 0.30`, `max_cross_slope: 0.20`, `min_step: 0.5` m. Un dénivelé inférieur à `min_step` n'est jamais signalé.

| Problème | Condition | Message |
|---|---|---|
| Saut (step) | Entre deux nœuds consécutifs d'une bordure, dénivelé ≥ `min_step` et pente > `max_slope` | `step between nodes A -> B: 183.18 -> 174.72 m (...)` |
| Pic (spike) | Les deux segments de part et d'autre d'un nœud sont des sauts de sens opposés (le nœud est plus haut ou plus bas que ses deux voisins). Seul ce nœud est signalé, pas les deux sauts | `spike at node N: 0.62 m above nodes A and B (...)` |
| Dévers (cross) | Entre un nœud d'une bordure de la lanelet et le point le plus proche de l'autre bordure (altitude interpolée linéairement le long de la bordure), dénivelé ≥ `min_step` et supérieur à `max_cross_slope` × leur distance. Un seul point par lanelet, celui au plus grand dénivelé ; non signalé si ce point ou le nœud correspondant de l'autre bordure est déjà signalé comme saut / pic | `cross slope at node N: 4.75 m below the right boundary 2.31 m away (...)` |
| Nœud sans `ele` | Des nœuds de bordure de la lanelet n'ont pas de `ele`. Une alerte par lanelet ; ces nœuds ne participent pas aux vérifications ci-dessus | `lanelet <id>: no elevation (ele) on nodes ..., not checked` |

Les points anormaux sont affichés par dénivelé décroissant ; leur numéro `#k` correspond à celui du graphique d'évolution de l'altitude.

## Extension `crosswalk`

Objet vérifié : lanelets `subtype=crosswalk`. Valeur actuelle : `max_extension: 0.5` m.

| Cas | Critère | Sortie |
|---|---|---|
| Dépassement excessif de la route | Une extrémité du passage piéton dépasse de la route de plus de `max_extension`. La route est l'union de toutes les lanelets véhicule qui chevauchent le passage piéton ; la mesure se fait dans le sens du passage piéton, avec une ligne tous les 0.1 m dans la largeur et un échantillon tous les 0.01 m le long de chaque ligne ; pour chaque extrémité, on retient le maximum sur toutes les lignes | `[WARNING] crosswalk <id>: extends up to 2.10 m beyond the road (max 0.50 m) \| ...` |
| Pas de dépassement excessif | ≤ `max_extension` ; une extrémité qui tombe à l'intérieur de la route vaut 0 | Seule la valeur mesurée est affichée, ce n'est pas un problème |
| Ne chevauche aucune voie | Ne chevauche aucune lanelet véhicule | Id seulement listé à la fin, non vérifié |

## Extension `_template` (exemple)

Son nom de répertoire commence par `_` : elle ne s'exécute jamais automatiquement, seulement avec `--ext _template`. Elle émet le WARNING `lanelet <id>: missing tag speed_limit` quand une lanelet véhicule n'a pas l'un des tags de `required_tags` (par défaut `speed_limit`).
