# Liste des vérifications MapChecker

[中文](checkinglist.md) | Français

Ce document liste tout ce que `check_lanelet_map.py` vérifie actuellement, et les conditions dans lesquelles chaque WARNING / ERROR est émis. Il est établi d'après le code de la version 3.3.0 (2026-11-04) ; les « valeurs actuelles » citées proviennent de `config/config.yaml` et de `ext/<nom>/config/config.yaml`. Après modification de la configuration, c'est le fichier de configuration qui fait foi.

## Vue d'ensemble

| # | Vérification (nom dans le Summary et la liste d'ignorés) | Objet vérifié | Quand elle s'exécute | Niveau |
|---|---|---|---|---|
| 1 | `turn_direction` | Lanelets véhicule | Toujours (sauf avec `--only-ext`) | WARNING |
| 2 | `right_of_way` | Paires de lanelets véhicule qui se chevauchent, éléments de régulation `right_of_way` | Toujours (sauf avec `--only-ext`) | WARNING / ERROR |
| 3 | `autoware` | Tout le fichier de carte | `autoware_checking: true` (actuellement `false`), nécessite de sourcer ROS | WARNING / ERROR |
| Extension | `altitude` | Altitude `ele` des nœuds des bordures des lanelets véhicule | `enable: true` (actuellement activée) | WARNING |
| Extension | `crosswalk` | Lanelets `subtype=crosswalk` | `enable: true` (actuellement activée) | WARNING |
| Extension | `stopline` | Ways `type=stop_line`, éléments de régulation `subtype=traffic_sign` / `road_marking` | `enable: true` (actuellement activée) | WARNING / ERROR |
| Extension | `border_sharing` | Paires de lanelets véhicule côte à côte | `enable: true` (actuellement désactivée) | WARNING |
| Extension | `trafficlight` | Éléments de régulation `subtype=traffic_light` | `enable: true` (actuellement activée) | WARNING / ERROR |
| Extension | `_template` | Tags des lanelets véhicule | Jamais automatiquement, seulement avec `--ext _template` | WARNING |

## Règles générales

- **Lanelet véhicule** : lanelet dont le `subtype` est `road`, `highway` ou `road_shoulder`. Les vérifications 1, 2, altitude et border_sharing ne portent que sur les lanelets véhicule ; trafficlight ne vérifie que l'usage des feux par les lanelets véhicule.
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
  - Passages piétons qui ne chevauchent aucune voie, lignes d'arrêt et ways `traffic_light` référencés par aucun élément de régulation, feux référencés uniquement par des lanelets non véhicule (par exemple les feux piétons des passages piétons) : seuls leurs id sont listés.
  - Le score `p` de `Using_LR`, les Hotspots, la carte de chaleur et le masque de ralentissement (`masque.enabled: true`, actuellement `false`) : ils ne font que noter, trier et compter les problèmes déjà affichés, sans en ajouter ni en masquer aucun dans le rapport. `map_min_p` (actuellement 0.5) ne décide que des problèmes dessinés sur la carte de chaleur ; le rapport et le log les listent toujours tous.

## Notions de base (communes aux vérifications 1 et 2)

| Notion | Critère |
|---|---|
| Virage | Sur un tronçon quelconque de longueur au plus `turn_window` (actuellement 30 m), la variation de cap de la ligne centrale (moyenne des bordures gauche et droite) est ≥ `turn_angle` (actuellement 30°). Le cap est d'abord lissé par une moyenne glissante d'environ 2 m, pour éliminer le bruit des angles aux nœuds ; pour une lanelet plus courte que `turn_window`, c'est l'angle sur toute sa longueur. Positif à gauche, négatif à droite |
| Ligne droite | Tout ce qui n'est pas un virage |
| Chevauchement | La surface de chevauchement de deux lanelets véhicule est ≥ `min_overlap_area` (actuellement 1.0 m², estimée par échantillonnage sur une grille de 0.5 m). Deux lanelets toutes deux droites, ou de sens opposés (écart de cap > 150° au chevauchement), qui se chevauchent sur une longueur ≥ `parallel_min_length` (actuellement 30 m) et dont les lignes centrales restent à ≥ `parallel_min_gap` (actuellement 1.8 m) l'une de l'autre le long du chevauchement sont des voies parallèles (par exemple sur une route étroite ou qui se rétrécit) et ne comptent pas comme chevauchement. Deux lanelets qui se suivent (l'une est le prédécesseur / successeur de l'autre) ou qui sont adjacentes (way de bordure partagé) ne comptent pas comme chevauchement |
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

## Extension `stopline`

Cette vérification n'a pas de paramètre.

**Géométrie** : tous les ways `type=stop_line` de la carte, qu'ils soient référencés ou non par un élément de régulation.

| Niveau | Condition | Message |
|---|---|---|
| WARNING | Plus de 2 nœuds (polyligne). Le nœud intermédiaire le plus éloigné de la droite entre les deux extrémités est indiqué avec sa distance : une distance de 0 signifie qu'il s'agit seulement d'un nœud superflu sur la droite, à supprimer | `stop_line <id>: 3 points (polyline), should be a straight line of 2 points; node N is 1.00 m off the line between its ends (length 7.3 m) \| lanelet <id> at (x, y)` |
| ERROR | Moins de 2 nœuds | `stop_line <id>: only 1 point(s), a stop line needs 2 \| ...` |
| ERROR | Référence à des nœuds absents de la carte | `stop_line <id>: references node(s) ... missing from the map \| ...` |

- `| lanelet ...` donne les lanelets qui référencent cette ligne d'arrêt, via le `ref_line` d'un élément de régulation ou le `refers` d'un road_marking, ou `not referenced by any regulatory element` s'il n'y en a pas.
- Avec `only_check_AOI: true`, seules les lignes d'arrêt entièrement situées dans une AOI sont vérifiées.

**Références** : les éléments de régulation `subtype=traffic_sign` et `subtype=road_marking`, avec les mêmes exigences que `regulatory_element_details_for_traffic_signs` et `road_markings` d'autoware_lanelet2_map_validator.

| Élément | Niveau | Condition | Message |
|---|---|---|---|
| traffic_sign | ERROR | Aucun way `type=traffic_sign` dans `refers` | `traffic_sign <id>: no traffic sign (refers way with type=traffic_sign), Autoware does not recognize it as a stop sign \| ...` |
| traffic_sign | ERROR | Panneau stop (`subtype=stop_sign`, ou pas de panneau) sans `ref_line` : le module stop_line d'Autoware s'arrête devant le `ref_line`. Si la ligne d'arrêt a été mise dans `refers`, le message indique de changer son rôle | `traffic_sign <id>: no stop line (ref_line): ... the vehicle does not stop at this sign; stop line way <s> is in role refers, change its role to ref_line \| ...` |
| traffic_sign | WARNING | `ref_line` présent, mais `refers` contient aussi une ligne d'arrêt | `traffic_sign <id>: stop line way <s> is in role refers, a stop line belongs in ref_line \| ...` |
| traffic_sign | WARNING | `refers` contient un way qui n'est ni un panneau ni une ligne d'arrêt | `traffic_sign <id>: refers way <w> has type=..., not traffic_sign \| ...` |
| traffic_sign | WARNING | Le way de `ref_line` n'est pas `type=stop_line` | `traffic_sign <id>: ref_line way <w> has type=..., not stop_line \| ...` |
| road_marking | ERROR | Pas exactement un `refers` | `road_marking <id>: 2 refers, a road_marking must reference exactly one line (its stop line) \| ...` |
| road_marking | WARNING | Le way de `refers` n'est pas `type=stop_line` : Autoware ne l'utilise comme ligne d'arrêt que s'il est `type=stop_line` (par exemple la ligne d'arrêt d'intersection du module intersection) | `road_marking <id>: refers way <w> has type=line_thin, not stop_line: Autoware does not use it as a stop line \| ...` |
| road_marking | WARNING | `ref_line` présent (sans effet) | `road_marking <id>: ref_line <w>: a road_marking references its stop line with refers only, its ref_line is not used \| ...` |
| Les deux | ERROR | Un membre n'est pas un way de la carte | `<subtype> <id>: refers <w> is not a way of the map \| ...` |

- `| lanelet ...` donne les lanelets qui référencent cet élément de régulation, ou `not referenced by any lanelet` ; le `at (x, y)` qui suit est le milieu de la ligne d'arrêt (y compris celle mise dans le mauvais rôle).
- Les panneaux autres que `stop_sign` n'ont pas besoin de `ref_line`.
- Avec `only_check_AOI: true`, seuls les éléments de régulation dont toutes les lignes sont entièrement situées dans une AOI sont vérifiés.

Dans la liste d'ignorés, on peut indiquer l'id du way de la ligne d'arrêt, l'id de l'élément de régulation ou d'un des ways qu'il référence, ou celui d'une lanelet qui les référence.

## Extension `border_sharing`

Objet vérifié : les bordures « face à face » de deux lanelets véhicule côte à côte. Dans le même sens, on compare la bordure droite de l'une à la bordure gauche de l'autre ; en sens opposés, les deux bordures gauches ou les deux bordures droites. Valeurs actuelles : `max_distance: 0.1` m, `min_fraction: 0.8`, `min_length: 2.0` m.

Un point est pris tous les 0.5 m le long d'une bordure. Il compte comme confondu quand sa distance à l'autre bordure est ≤ `max_distance` et que les deux bordures sont parallèles (antiparallèles en sens opposés, à environ 25° près).

| Cas | Critère | Sortie |
|---|---|---|
| Voies de même sens sans bordure partagée | Les deux bordures sont des ways différents, et la partie confondue est ≥ `min_length` et ≥ `min_fraction` de la longueur de la plus courte des deux bordures | `[WARNING] lanelet <a> left (way ..) / lanelet <b> right (way ..): boundaries coincide over 21.0 m (100% of the shorter) but are different ways, gap 0.26 m between the lanes; they are not adjacent in the routing graph, no lane change between them \| at (x, y)` |
| Voies opposées sans ligne centrale partagée | Idem, les deux voies sont de sens opposés | `[WARNING] lanelet <a> left (way ..) / lanelet <b> left (way ..), opposite directions: boundaries coincide over ... but are different ways, ...; opposite lanes should share one center line \| at (x, y)` |
| Déjà partagée | Les deux bordures sont le même way | Seulement comptée, non signalée |
| Simplement proches par endroits | La partie confondue n'atteint pas la longueur / la proportion ci-dessus, par exemple des voies de virage qui se croisent dans une intersection | Non signalé |

- La position relative des deux lignes est la médiane de la distance signée sur la partie confondue : `drawn on top of each other` (superposées), `gap x m between the lanes` (espace entre les voies), `overlap x m between the lanes` (voies qui se chevauchent).
- Avec `only_check_AOI: true`, seules les paires dont les deux lanelets sont entièrement situées dans une AOI sont vérifiées.

## Extension `trafficlight`

Objet vérifié : éléments de régulation `subtype=traffic_light`, vérifiés selon l'usage qu'en font réellement le module traffic_light d'Autoware et la mise en correspondance caméra de milla (`milla_traffic_light_matching`). Les conséquences de chaque problème sont décrites dans [ext/trafficlight/README.md](../ext/trafficlight/README.md).

Valeurs actuelles : `max_facing_angle: 40`°, `min_forward_distance: 1.0` m, `min_height: 0.5` / `max_height: 5.0` m, `stop_line_extend_length: 5.0` m, `same_direction_angle: 45`°, `check_light_bulbs: false` (`bulb_max_offset: 1.0` m).

| Notion | Critère |
|---|---|
| Point d'arrêt, direction de la voie | Premier point d'intersection de la ligne centrale de la voie avec la ligne d'arrêt (premier `ref_line`), et direction de la ligne centrale en ce point. Sans intersection sur cette lanelet, on cherche sur ses successeurs, puis ses prédécesseurs |
| Feu reconnaissable | Feu `traffic_light_type=camera` : sa direction (direction de la linestring + 90°, c'est-à-dire dessinée de gauche à droite vue depuis la ligne d'arrêt) diffère de celle de la voie d'au plus `max_facing_angle`, et le centre du boîtier est à ≥ `min_forward_distance` devant le point d'arrêt, le long de la voie. Un feu `v2x` est toujours considéré comme reconnaissable |
| Hauteur du feu | Hauteur (m) du centre du boîtier (altitude de la linestring du feu + `height` / 2) au-dessus de la ligne d'arrêt (moyenne des `ele` de ses nœuds). Non vérifiée sans `ele` |

**Reconnaissance et ligne d'arrêt** (vérifiées pour chaque lanelet véhicule qui utilise ce feu) :

| Niveau | Condition | Message |
|---|---|---|
| ERROR | Aucun feu n'est reconnaissable depuis cette voie : les feux caméra sont tous mal orientés ou pas devant le point d'arrêt, et il n'y a pas de feu `v2x` | `traffic_light <id> \| lanelet <id>: no light can be recognized from the lanelet (light <w>: faces away, facing error +175° (max 40°); drawn right -> left, reverse its node order): ...` ; sans feu `camera` / `v2x`, la parenthèse contient `no light with traffic_light_type camera or v2x` |
| WARNING | Certains feux sont reconnaissables, mais un feu caméra ne l'est pas depuis certaines voies. Une alerte par feu | `traffic_light <id>: traffic light way <w> cannot be recognized from lanelet <ids> (<raison>); only the other light(s) of the regulatory element are used there` |
| ERROR | La ligne d'arrêt ne croise pas la ligne centrale de la voie (ni de ses prédécesseurs / successeurs), même prolongée de `stop_line_extend_length` à chaque extrémité | `traffic_light <id> \| lanelet <id>: stop line <s> does not cross the lanelet (nor its predecessors / successors), even extended by 5 m: ...` |
| WARNING | La ligne d'arrêt ne croise la voie qu'une fois prolongée | `traffic_light <id> \| lanelet <id>: stop line <s> only crosses the lanelet once extended by 5 m (stop_line_extend_length) at (x, y)` |

Raisons de non-reconnaissance : `faces away, facing error +x° (max 40°)` (suivi de `drawn right -> left, reverse its node order` quand le feu serait reconnu dessiné dans l'autre sens), `x m behind the stop line`, `only x m ahead of the stop line (min 1 m)`.

**Relation avec les autres voies** :

| Niveau | Condition | Message |
|---|---|---|
| ERROR | Un feu (way de `refers`) est référencé par plusieurs éléments de régulation traffic_light | `traffic light way <w> is referenced by traffic_light <a>, <b>: the camera matching keeps a single (light, regulatory element) pair per light, ...` |
| WARNING | La ligne d'arrêt prolongée croise la ligne centrale d'une autre lanelet véhicule proche, dont la direction au point d'intersection diffère de moins de `same_direction_angle` de celle des voies qui utilisent ce feu. Cette lanelet n'est ni une voie qui l'utilise ni l'un de ses prédécesseurs / successeurs, et ne référence aucun élément de régulation partageant un feu avec lui | `traffic_light <a> (lanelet <ids>): stop line <s> crosses lanelet <o> (same direction, once extended by 5 m) at (x, y), which uses traffic_light <b>: ...` ; `which uses no traffic light: ...` si cette lanelet n'a pas de feu ; `the stop line itself` si elle la croise sans prolongement |

**Structure et tags** :

| Niveau | Condition | Message |
|---|---|---|
| ERROR | Pas de `refers` ; le way de `refers` n'existe pas, a des nœuds manquants ou moins de 2 points | `no traffic light (refers)` / `traffic light way <w> missing from the map` / `traffic light way <w> with missing nodes or fewer than 2 points` |
| WARNING | Le way de `refers` n'est pas `type=traffic_light` | `refers way <w> has type=..., not traffic_light` |
| ERROR | Pas de `ref_line` ; le way du premier `ref_line` n'existe pas ou est invalide | `no stop line (ref_line): ...` / `stop line way <s> is missing from the map or invalid` |
| WARNING | Plusieurs `ref_line` (seul le premier est utilisé) ; le way de `ref_line` n'est pas `type=stop_line` | `2 ref_line, only the first one (way <s>) is used as stop line` / `ref_line way <s> has type=..., not stop_line` |
| WARNING | Référencé par aucune lanelet | `not referenced by any lanelet, no traffic_light module is ever created for it` |
| WARNING | Feu sans `traffic_light_type`, ou dont la valeur n'est ni `camera` ni `v2x` | `traffic light way <w> has no traffic_light_type: ...` / `traffic light way <w> has traffic_light_type=..., used neither by the camera matching (camera) nor by the V2X handler (v2x)` |
| WARNING | Feu caméra sans `height` valide (pas un nombre positif) | `camera traffic light way <w> has no valid height tag, the camera matching projects a box of height 0` |
| WARNING | Hauteur du feu hors de `min_height` ~ `max_height` | `traffic light way <w> is 0.72 m above the stop line (expected 0.5 ~ 5 m), ...` |

**Ampoules** (avec `check_light_bulbs: true` ; actuellement `false`, non vérifiées) :

| Niveau | Condition | Message |
|---|---|---|
| WARNING | Les ampoules (nœuds de `light_bulbs`) sont à plus de `bulb_max_offset` du boîtier de feu le plus proche. Le boîtier s'étend de la linestring du feu jusqu'à `height` m au-dessus | `light_bulbs way <b> is up to 2.81 m from traffic light way <w> (max 1 m): bulbs z ... ~ ..., light z ... ~ ...` |
| ERROR | Le way de `light_bulbs` n'existe pas ou a des nœuds manquants | `light_bulbs way <b> is missing from the map or has missing nodes` |

- Les messages des tableaux Structure et tags et Ampoules sont précédés de `traffic_light <id>: `. Les id d'un problème sont l'élément de régulation et les lanelets concernées ; il suffit d'indiquer l'un d'eux dans la liste d'ignorés.
- Avec `only_check_AOI: true`, seuls les feux utilisés par une lanelet véhicule entièrement située dans une AOI sont vérifiés.
- Indépendante des points sur les feux de la vérification 3 (light_bulb, `height`) : ceux-ci sont signalés par l'outil de validation d'Autoware.

## Extension `_template` (exemple)

Son nom de répertoire commence par `_` : elle ne s'exécute jamais automatiquement, seulement avec `--ext _template`. Elle émet le WARNING `lanelet <id>: missing tag speed_limit` quand une lanelet véhicule n'a pas l'un des tags de `required_tags` (par défaut `speed_limit`).
