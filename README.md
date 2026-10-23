# MapChecker

[中文](doc/README.zh.md) | Français

Outil de vérification des cartes Lanelet2 : il repère les endroits de la carte où manquent un `turn_direction` (clignotant) ou un `right_of_way` (priorité), peut fusionner les résultats de la vérification officielle d'Autoware, et peut noter les alertes par régression logistique (logistic regression) pour distinguer celles qui sont probablement de vrais problèmes de celles qui sont probablement des faux positifs.

## Vérifications

| Vérification | Contenu |
|---|---|
| 1. turn_direction | Les virages / tournants, ainsi que les lanelets en ligne droite qui intersectent d'autres voies (croisement / convergence), ont-ils un `turn_direction` ? |
| 2. right_of_way | Les paires de lanelets qui se croisent / convergent ont-elles un élément de régulation `right_of_way` ? Les `right_of_way` existants sont-ils référencés par la lanelet prioritaire, et les deux lanelets se chevauchent-elles réellement (ERROR si elles ne se chevauchent pas du tout) ? |
| 3. Autoware (`autoware_checking: true`) | Exécute [utils/validate_autoware_map.py](utils/validate_autoware_map.py) (version Python de `autoware_lanelet2_validation`) : erreurs de chargement de la carte, altitude `ele` des nœuds, feux de circulation, `turn_direction` des lanelets en conflit dans le graphe de routage. Les résultats `turn_direction` sont fusionnés dans la vérification 1 ; une même lanelet n'est signalée qu'une fois |
| Notation LR (`Using_LR: true`) | Attribue un score `[WARNING p=0.77]` aux alertes des vérifications 1 et 2 signalant un `turn_direction` / `right_of_way` manquant, et les affiche par score décroissant, voir [Notation des alertes](#notation-des-alertes-using_lr) |
| Extensions (`enable: true` dans `ext/<nom>/config/config.yaml`) | Par exemple, de combien un passage piéton (crosswalk) dépasse au plus latéralement de la route, voir [Extensions](#extensions-ext) |
| Carte de chaleur (`heatmap.enabled: true`) | Liste les zones à forte concentration de problèmes et génère une carte de chaleur : la carte est colorée selon la densité de problèmes, du bleu (peu) au rouge (beaucoup), voir [Carte de chaleur des problèmes](#carte-de-chaleur-des-problèmes-heatmap) |

## Dépendances

| Usage | Nécessite |
|---|---|
| Vérifications 1 et 2, notation LR | Python 3.10+, `numpy`, `PyYAML` |
| Carte de chaleur | `matplotlib` |
| Vérification 3 (Autoware) | ROS 2 Humble et `autoware_lanelet2_extension_python` ; sourcer avant l'exécution :<br>`source /opt/ros/humble/setup.bash`<br>`source ~/ros/ad_ros2_ws/install/setup.bash` |
| Entraînement du modèle de notation | `scikit-learn` (et sa dépendance `scipy`), `git` (seulement pour `train_model.py`) ; si `autoware_checking: true` dans `config/dataset.yaml`, il faut aussi sourcer ROS |

Les dépendances autres que ROS / Autoware s'installent en une fois avec `./install.sh` (Ubuntu 22.04) : apt installe `python3-pip`, `python3-tk` (fenêtre de la carte de chaleur) et `git` ; pip installe `numpy`, `PyYAML`, `matplotlib`, `scipy`, `scikit-learn` et toutes leurs dépendances dans le répertoire `libs/` du projet, sans toucher au système ni au répertoire utilisateur. Au démarrage, `check_lanelet_map.py` et `train_model.py` placent `libs/` en tête de `sys.path`. Chaque exécution réinstalle tout et remplace `libs/` en entier ; comme il contient des bibliothèques compilées, il ne fonctionne qu'avec la version de Python utilisée lors de l'installation. `numpy` est limité à `< 2` pour ne pas casser les paquets Python de ROS 2 Humble.

## Démarrage rapide

```bash
cd MapChecker
python3 check_lanelet_map.py                          # utilise la carte et les réglages de config/config.yaml
python3 check_lanelet_map.py path/to/lanelet2_map.osm # vérifie une autre carte
python3 check_lanelet_map.py -c other_config.yaml     # utilise un autre fichier de configuration
python3 check_lanelet_map.py --no-autoware-checking   # sans environnement ROS
python3 check_lanelet_map.py --only-ext crosswalk     # n'exécute que l'extension crosswalk (voir ext/README.md)
```

Les options de ligne de commande sont prioritaires sur le fichier de configuration :

| Option | Configuration correspondante |
|---|---|
| `map` (argument positionnel) | `input` |
| `-c, --config FILE` | Fichier de configuration, par défaut `config/config.yaml` |
| `--turn-angle DEG` | `turn_angle` |
| `--turn-window M` | `turn_window` |
| `--min-overlap-area M2` | `min_overlap_area` |
| `--parallel-min-length M` | `parallel_min_length` |
| `--parallel-min-gap M` | `parallel_min_gap` |
| `--[no-]ignore-isolated-turns` | `ignore_isolated_turns` |
| `--[no-]straight-as-turn-light` | `straight_as_turn_light` |
| `--[no-]only-check-aoi` | `only_check_AOI` |
| `--[no-]autoware-checking` | `autoware_checking` |
| `--ignore-file FILE` | `ignore_file` |
| `--[no-]using-lr` | `Using_LR` |
| `--model-file FILE` | `model_file` |
| `--map-min-p P` | `map_min_p` |
| `--[no-]heatmap` | `heatmap.enabled` |
| `--[no-]show-heatmap` | `isShow` |
| `--no-color` | Sortie sans couleurs |
| `--ext NAME[,NAME...]` | Exécute ces extensions, à la place de celles qui ont `enable: true` dans leur configuration |
| `--only-ext NAME[,NAME...]` | N'exécute que ces extensions, sans les vérifications 1 à 3 |
| `--no-ext` | N'exécute aucune extension |
| `--set NAME.KEY=VALUE` | Remplace un paramètre de la configuration d'une extension, par exemple `crosswalk.max_extension=1.0`, répétable |
| `--list-ext` | Liste toutes les extensions et indique si elles sont activées |

## Configuration `config/config.yaml`

Les chemins relatifs sont relatifs à `MapChecker/`.

| Paramètre | Valeur par défaut | Description |
|---|---|---|
| `input` | `lanelet2_map.osm` | Carte à vérifier |
| `ignore_file` | `config/ignore.yaml` | Lanelets ignorées manuellement ; si le fichier n'existe pas, rien n'est ignoré |
| `model_file` | `config/model.yaml` | Modèle de notation des alertes, utilisé avec `Using_LR: true` |
| `log.enabled` / `log.dir` | `false` / `logs` | Écrit, en plus de la sortie terminal, le log `logs/map_check_YYYYmmdd_HHMMSS.log` |
| `heatmap.enabled` | `false` | `true` : liste en fin de rapport les zones à forte concentration de problèmes et génère dans `log.dir` une carte de chaleur du même nom `map_check_YYYYmmdd_HHMMSS.png` ; nécessite matplotlib |
| `heatmap.radius` | `30.0` | Rayon des zones (m) : les problèmes distants d'au plus `radius` appartiennent à la même zone ; la diffusion de la carte de chaleur est d'environ `radius` |
| `heatmap.top` | `10` | Nombre maximal de zones annotées (une zone doit compter au moins 2 problèmes) |
| `turn_angle` | `30.0` | Variation de cap minimale pour qu'il y ait virage (degrés) |
| `turn_window` | `30.0` | Fenêtre maximale de longueur d'arc pour calculer la variation de cap (m) ; une courbe douce sur une longue lanelet ne compte pas comme virage |
| `min_overlap_area` | `1.0` | Surface minimale pour que deux lanelets se chevauchent (m²) |
| `parallel_min_length` | `10.0` | Les voies parallèles ne comptent pas comme chevauchement : deux lanelets toutes deux droites, ou de sens opposés (écart de cap > 150° au chevauchement), qui se chevauchent sur une longueur (de la zone de chevauchement dans sa direction principale) ≥ cette valeur (m), et dont les lignes centrales restent à ≥ `parallel_min_gap` l'une de l'autre le long du chevauchement, sont des voies parallèles (route étroite ou qui se rétrécit, chevauchement le long de la route), pas un croisement / une convergence |
| `parallel_min_gap` | `1.5` | Distance minimale (m) entre les lignes centrales des voies parallèles ci-dessus ; une très grande valeur (par exemple `99`) désactive l'exclusion |
| `ignore_isolated_turns` | `false` | `true` : le `turn_direction` des virages qui ne croisent pas d'autres voies et ne convergent pas avec elles (une divergence ne compte pas) n'est pas vérifié |
| `straight_as_turn_light` | `true` | `true` : `straight` compte aussi comme clignotant ; les virages et les lanelets droites qui intersectent doivent avoir un `turn_direction`, la direction n'est pas vérifiée<br>`false` : vérifie que la direction correspond à la géométrie ; les lanelets droites n'ont pas à être marquées `straight` |
| `only_check_AOI` | `false` | `true` : ne vérifie que les lanelets entièrement situées dans les AOI de la carte (way fermé `subtype=AOI`) ; si la carte n'a pas d'AOI, toute la carte est vérifiée |
| `autoware_checking` | `false` | `true` : exécute la vérification 3, nécessite ROS |
| `isShow` | `false` | `true` : ouvre la fenêtre de la carte de chaleur à la fin du rapport (zoom, déplacement) ; le programme se termine à la fermeture de la fenêtre. Sans effet si `heatmap.enabled` n'est pas `true` ; sans interface graphique (par exemple en ssh), seul un message est affiché |
| `Using_LR` | `false` | `true` : note les alertes avec `model_file` ; chaque vérification les affiche par score décroissant. Erreur si le fichier de modèle n'existe pas |
| `map_min_p` | `0.0` | Avec `Using_LR` : les alertes notées dont le p est inférieur ne sont pas affichées sur la carte de chaleur (points, coloration, zones, fenêtre) ; le rapport et le log les listent toujours. `0` affiche tout |

La valeur par défaut est celle utilisée lorsque le paramètre est absent du fichier de configuration ; le `config/config.yaml` du dépôt active `log`, `heatmap`, `ignore_isolated_turns`, `only_check_AOI`, `autoware_checking` et `Using_LR`.

## Liste d'ignorés `config/ignore.yaml`

Les lanelets dont on a confirmé qu'elles n'ont pas besoin d'être corrigées sont listées ici ; les alertes correspondantes ne sont plus affichées, et la fin du rapport indique combien de problèmes chaque entrée a masqués.

```yaml
all:            [13252: "roule pas", 17618]                          # ignorées par toutes les vérifications
turn_direction: [614: "virage de route principale, pas de clignotant"]   # ignorée uniquement par la vérification 1
right_of_way:   []        # uniquement par la vérification 2 (paires qui se chevauchent et éléments de régulation right_of_way impliquant la lanelet)
autoware:       []        # uniquement par la vérification 3 (problèmes des feux référencés par la lanelet)
crosswalk:      [19942]   # uniquement par l'extension crosswalk (la clé est le nom de l'extension)
```

Chaque entrée peut être un simple id de lanelet (éventuellement négatif) ou `id: raison`. La liste d'ignorés d'une extension peut aussi être écrite dans sa propre configuration. En fin de rapport, les entrées des vérifications qui n'ont pas été exécutées sont marquées `check not run`.

## Sortie

- `[WARNING]` (jaune) : problème à confirmer ; `[ERROR]` (rouge) : erreur certaine, par exemple deux lanelets d'un `right_of_way` qui ne se chevauchent pas du tout, ou une erreur de chargement de la carte.
- La vérification 1 affiche les alertes dans l'ordre des id de lanelet. La vérification 2 affiche d'abord les problèmes des éléments de régulation `right_of_way` existants, puis les alertes de `right_of_way` manquant, groupées par zone (en général une intersection), avec pour chaque groupe le centre et les lanelets concernées.
- Avec `Using_LR: true`, l'ordre est différent, voir [Ordre de sortie](#ordre-de-sortie).
- En fin de rapport, le Summary donne le nombre d'alertes de chaque vérification, les Hotspots listent les zones à forte concentration de problèmes (avec `heatmap.enabled: true`, voir [Carte de chaleur des problèmes](#carte-de-chaleur-des-problèmes-heatmap)), et Ignored lanelets indique l'usage de la liste d'ignorés (absente de la carte, hors AOI, nombre de problèmes masqués).
- Code de sortie : `0` aucun problème, `1` au moins un WARNING / ERROR non ignoré, `2` erreur d'option ou d'environnement. Utilisable en CI.

La vérification Autoware peut aussi être exécutée seule (après avoir sourcé ROS) :

```bash
python3 utils/validate_autoware_map.py lanelet2_map.osm
```

## Carte de chaleur des problèmes (`heatmap`)

Les alertes d'une carte se concentrent souvent sur quelques intersections. Avec `heatmap.enabled`, la fin du rapport liste les zones qui ont le plus de problèmes, et un `.png` du même nom que le log est généré dans le répertoire des logs, pour traiter en premier les endroits où les problèmes sont les plus concentrés :

```
=== Hotspots (at least 2 issues within 30 m) ===
#1  center (67932.7, 6497.8): 16 issues (turn_direction 8, right_of_way 8), lanelets: 3973 4060 4081 4095 4102 4107 4134 4167 4202 4313
#2  center (68200.6, 6151.5): 13 issues (right_of_way 9, turn_direction 4), lanelets: 2690 3668 3676 3690 3698 5595 6165 6811 6866
...
Heatmap: .../logs/map_check_20261016_093357.png
```

- **Position** : une alerte de la vérification 1 est placée au milieu de la ligne centrale de la lanelet (le `middle` du log), une alerte de `right_of_way` manquant de la vérification 2 au centre de la zone de chevauchement, les autres alertes à la moyenne des milieux des lanelets concernées ; les alertes sans lien avec une lanelet (nœud sans altitude, erreur de chargement de la carte) ne sont pas prises en compte. Les alertes ignorées ne sont pas prises en compte.
- **Couleur** : densité par noyau gaussien (sigma = `radius / 2`), dont la valeur correspond à peu près au nombre de problèmes voisins. Un problème isolé apparaît en bleu ; plus les problèmes sont denses, plus la couleur tire vers le rouge. Les endroits sans problème sont transparents et laissent voir la carte.
- **Zones** : on prend à chaque fois la position qui a le plus de problèmes restants dans un rayon de `radius` mètres comme zone ; les problèmes de cette zone ne comptent plus pour les zones suivantes. On continue jusqu'à avoir `heatmap.top` zones ou jusqu'à ce qu'aucune position restante n'ait au moins 2 problèmes. Sur l'image, les zones sont entourées d'un cercle et annotées `#rang nombre de problèmes`, avec la même numérotation que dans le log.
- **Seulement les problèmes de p élevé** (`Using_LR: true`) : avec `map_min_p` > 0, les alertes notées dont le p est inférieur ne sont pas dessinées et n'interviennent ni dans la coloration ni dans les zones ; les Hotspots et le titre de la figure indiquent combien sont masquées, le rapport et le log ne changent pas.
- **Pondération par p** (`Using_LR: true`) : une alerte notée compte pour p, une alerte non notée et une ERROR comptent pour 1 ; la couleur, la détermination des zones et le classement utilisent ce nombre pondéré, c'est-à-dire le nombre attendu de vrais problèmes. Les zones que le modèle juge surtout composées de faux positifs ont une couleur plus pâle, sont classées plus bas ou ne sont plus listées. Le log donne à la fois le nombre et la valeur pondérée, l'image annote la valeur pondérée :
  ```
  === Hotspots (at least 2 p-weighted issues within 30 m) ===
  #1  center (67936.5, 6494.3): 14 issues, p-weighted 9.4 (right_of_way 9, turn_direction 5), lanelets: 3973 3980 4060 4107 4134 4167 4202 4313
  ```
- **Fenêtre** : avec `isShow: true`, la fenêtre de la carte de chaleur s'ouvre une fois le rapport et le log écrits ; la barre d'outils permet de zoomer sur une zone. Le programme se termine à la fermeture de la fenêtre, avec le même code de sortie. Les extensions peuvent aussi avoir leur propre fenêtre (par exemple `isShow` dans la configuration d'altitude), qui s'ouvre en même temps que celle de la carte de chaleur ; le programme se termine une fois toutes les fenêtres fermées.
  - Clic sur un point de problème : affiche le numéro de lanelet, la vérification et le p du problème à cet endroit (tous, s'il y en a plusieurs au même endroit), entoure en orange les lanelets concernées, et affiche dans le terminal les lignes correspondantes du log (pratique pour les copier).
  - Clic sur une entrée de la légende (par exemple `stopline (1, ext)`, `ERROR (12)`, `hotspot`) : la vue se déplace sur le problème suivant de cette catégorie (ou la zone suivante) et affiche ses informations, comme un clic sur un point de problème ; chaque clic passe au suivant, l'étiquette indique `k/total`, et après le dernier on revient au premier. L'ordre est celui de la sortie ; le bouton précédent de la barre d'outils revient à la vue précédente, le bouton d'accueil à toute la carte.
  - Clic ailleurs : affiche le numéro de la lanelet à l'endroit cliqué (plusieurs possibles en cas de chevauchement) ; un clic dans le vide masque l'info.
  - Le clic fonctionne aussi en mode zoom / déplacement ; un glisser ne compte pas comme un clic.
- **Étendue** : avec `only_check_AOI: true`, seule l'étendue des AOI est dessinée (bordure des AOI en pointillés), sinon toute la carte. Les points de problème ont une forme selon la vérification : cercle turn_direction, carré right_of_way, triangle autoware. Les problèmes des extensions (`ext/`) sont marqués par des points verts (une forme par extension) ; ils n'interviennent ni dans la coloration ni dans la détermination des zones, et leur nombre est indiqué à part dans les Hotspots. Les ERROR de toutes les vérifications (extensions comprises) sont marquées par des points rouges, dessinés au-dessus des autres points.

## Notation des alertes (`Using_LR`)

### Rôle

Une carte compte souvent des centaines d'alertes, dont une partie que l'équipe cartographie juge inutile de corriger. Avec `Using_LR`, les alertes des vérifications 1 et 2 signalant un `turn_direction` / `right_of_way` manquant reçoivent un score `p`, la probabilité que l'alerte soit un vrai problème, c'est-à-dire **la probabilité que l'équipe cartographie la corrige par la suite**. Le Summary indique le nombre d'alertes avec `p < 0.5`. Le score sert seulement à décider quelles alertes regarder en premier : toutes les alertes restent affichées.

### Ordre de sortie

Dans le terminal et le log, les alertes notées de chaque vérification sont affichées par `p` décroissant (à `p` égal, dans l'ordre d'origine) :

- Vérification 1 : les alertes sans score (par exemple une valeur de `turn_direction` invalide) viennent en premier, puis les alertes triées par `p`.
- Vérification 2 : d'abord les problèmes des éléments de régulation `right_of_way` existants (sans score), puis la liste de toutes les zones, et enfin les alertes de `right_of_way` manquant de toutes les zones, triées ensemble par `p`, chacune indiquant sa zone `(area k)`.

```
=== Check 1: turn_direction on curves / turns (angle threshold 30°, window 30 m) ===
[WARNING p=0.77] lanelet 12528: curve without turn_direction (geometry turns right, suggest turn_direction=right) | ...
[WARNING p=0.77] lanelet 17361: curve without turn_direction (geometry turns right, suggest turn_direction=right) | ...
...
[WARNING p=0.07] lanelet 13646: intersects other lanes without turn_direction (geometry straight, ...) | ...

=== Check 2: right_of_way on overlapping lanelets (min overlap area 1.0 m²) ===
-- Area 1, center (30772.0, 10528.6), lanelets: 9 14 445 450 10735 10754
...
-- Area 26, center (30572.3, 11333.2), lanelets: 16541 16550 17931 17936
[WARNING p=0.84] lanelet 12494 / 19041 (area 12): merge overlap (~13.5 m² at (31465.0, 9665.5)) without right_of_way
[WARNING p=0.69] lanelet 14182 / 17379 (area 22): merge overlap (~12.9 m² at (30830.3, 11337.8)) without right_of_way
...
```

### Méthode de calcul

Le modèle est une régression logistique : `p = 1 / (1 + e^-z)`, `z = intercept + Σ coef × (caractéristique - mean) / scale`. Ces nombres sont stockés dans `config/model.yaml` et le calcul se fait directement pendant la vérification, sans scikit-learn.

`model.yaml` enregistre aussi la plage de valeurs (`min` / `max`) de chaque caractéristique dans les données d'entraînement. Si une caractéristique est constante dans les données d'entraînement et qu'une alerte a une valeur différente (par exemple, les données d'entraînement ne contiennent aucune alerte détectée uniquement par Autoware, donc `isolated` vaut toujours 0), le modèle n'a jamais vu ce type d'alerte : l'alerte n'est pas notée, plutôt que de recevoir un score extrapolé.

### Entraîner / mettre à jour le modèle

Les données d'entraînement proviennent de l'historique git des dépôts de cartes : les vérifications 1 et 2 sont exécutées sur chaque version historique de la carte de chaque dépôt (avec aussi la vérification Autoware si `autoware_checking: true` dans `config/dataset.yaml`), et on suit le devenir de chaque alerte : corrigée, ignorée ou toujours présente.

Les alertes `turn_direction` détectées uniquement par Autoware (par exemple les lanelets d'une divergence, que le graphe de routage d'Autoware considère aussi comme un conflit) sont très différentes des alertes de la vérification géométrique ; elles doivent figurer dans les données d'entraînement pour que le modèle puisse les noter correctement. C'est pourquoi la génération des données d'entraînement exécute par défaut la vérification Autoware, ce qui nécessite de sourcer ROS au préalable ; si elle est désactivée, ce type d'alerte n'est pas noté pendant la vérification (voir [Méthode de calcul](#méthode-de-calcul)).

1. Lister dans [config/dataset.yaml](config/dataset.yaml) les dépôts de cartes et, le cas échéant, le fichier ignore correspondant à chaque dépôt.
2. Générer les données d'entraînement, entraîner et évaluer, ce qui produit `config/model.yaml` :

   ```bash
   python3 train_model.py                # --C 0.1 pour renforcer la régularisation
   python3 train_model.py --skip-build   # entraîne directement sur les données existantes de data/ sans les régénérer (par exemple pour ne changer que --C)
   ```

   `train_model.py` rejoue d'abord l'historique des dépôts de cartes avec [utils/build_dataset.py](utils/build_dataset.py) pour générer les données d'entraînement `data/turn_direction.csv` et `data/right_of_way.csv` (déjà dans `.gitignore`), puis entraîne avec [utils/tranning.py](utils/tranning.py).

Après de nouveaux commits dans les dépôts de cartes, une mise à jour des fichiers ignore ou une modification des caractéristiques dans [utils/features.py](utils/features.py), il suffit de relancer `train_model.py`. Les seuils de vérification (`turn_angle`, etc.) sont ceux de `config/config.yaml` ; il faut aussi réentraîner après les avoir modifiés.

### Règles d'étiquetage

| label | Condition |
|---|---|
| 1 (vrai problème) | L'alerte disparaît dans une version ultérieure, parce qu'un `turn_direction` a été ajouté / un `right_of_way` couvrant cette paire de lanelets a été ajouté |
| 0 (faux positif) | L'alerte est toujours présente dans la dernière version et la lanelet concernée est dans le fichier ignore ; ou bien, après l'apparition de l'alerte, au moins `min_fix_rounds` (2 par défaut) versions du même dépôt ont corrigé d'autres alertes de la même vérification, mais jamais celle-ci |
| Non étiquetée | L'alerte disparaît parce que la lanelet a été supprimée / sa géométrie a changé, ou elle est toujours présente dans la dernière version sans remplir les conditions ci-dessus (peut-être simplement pas encore corrigée) |

Les corrections sont comptées en nombre de versions et non en nombre d'alertes, car un seul commit en corrige souvent des dizaines d'un coup. Les dépôts qui n'ont qu'un seul label (par exemple toutes les alertes corrigées) ne participent pas à l'entraînement, sinon le modèle apprendrait la différence « quelle carte » au lieu de « quelle alerte ».

### Caractéristiques

| Vérification | Caractéristiques |
|---|---|
| turn_direction | `abs_angle` variation de cap (degrés), `curve` virage ou non, `log_length` longueur, `n_crossing` / `n_merge` / `n_diverge` nombre de lanelets avec lesquelles elle a un vrai croisement / une convergence / une divergence, `log_overlap_length` plus grande longueur de chevauchement avec une autre lanelet (divergences exclues ; longueur de la zone de chevauchement dans sa direction principale), `traffic_light` la lanelet ou son prédécesseur référence un feu de circulation, `isolated` n'a de vrai croisement / de convergence avec aucune voie, c'est-à-dire pas d'intersection |
| right_of_way | `log_area` surface de chevauchement, `merge` convergence ou croisement, `heading_diff` écart de cap au chevauchement, `max_abs_angle` plus grande variation de cap des deux lanelets |

**Vrai croisement, voies parallèles et contact de bord** : certains chevauchements ne sont pas des intersections. Sur une route étroite ou qui se rétrécit, deux voies droites se chevauchent le long de la route : par exemple, les lignes centrales de deux voies opposées ne sont qu'à environ 2 m l'une de l'autre, mais les voies sont dessinées plus larges que la chaussée, d'où une bande de chevauchement de plusieurs dizaines de mètres. Une intersection de plusieurs dizaines de mètres est pratiquement impossible ; dans un vrai croisement / une vraie convergence, les deux lignes centrales se rejoignent. Sur 15 cartes, 167 paires de lanelets droites se chevauchent sur ≥ 10 m ; pour les 40 que l'équipe a couvertes par un `right_of_way`, la distance minimale entre les lignes centrales le long du chevauchement ne dépasse pas 1.02 m (les voies se rejoignent). Ainsi, lorsque deux lanelets toutes deux droites (variation de cap < `turn_angle`), ou de sens opposés (écart de cap > 150° au chevauchement, par exemple les deux sens d'une route dans un virage), se chevauchent sur une longueur ≥ `parallel_min_length` (10 m par défaut) et que leurs lignes centrales restent à ≥ `parallel_min_gap` (1.5 m par défaut) l'une de l'autre le long du chevauchement, ce sont des voies parallèles et non un chevauchement, et les vérifications 1 et 2 ne les traitent plus comme un croisement (`is_parallel` dans `utils/context.py`) ; 58 paires sont ainsi exclues (dont 11 impliquant un virage, comme 9700/9710 dans cern), aucune n'étant couverte par un `right_of_way` de l'équipe. Avec un virage, seules les voies de sens opposés sont exclues : parmi les virages de même sens qui longent une voie voisine (une voie de virage qui frôle la voie voisine avant / après le virage), certains sont couverts par un `right_of_way` de l'équipe, avec des lignes centrales jusqu'à 1.8 m l'une de l'autre. La largeur du chevauchement ne suffit pas à les distinguer : par exemple -99869/4029 dans le_plessis, où 4029 est dessinée avec 4.2 m de large, se chevauchent sur 1.5 m de large et 60 m de long, mais leurs lignes centrales restent à 1.8 à 2.1 m l'une de l'autre. Les chevauchements de moins de 10 m ne sont pas exclus : parmi les chevauchements parallèles plus courts, certains sont couverts par un `right_of_way` de l'équipe. Les autres contacts de bord (par exemple un virage au début d'une divergence qui frôle la voie opposée) sont distingués par les caractéristiques : il n'y a vrai croisement que si la ligne centrale d'au moins une des lanelets entre dans l'emprise de l'autre (le véhicule entre dans la voie de l'autre) (`centerline_enters` dans `utils/geometry.py`).

Le dénivelé entre nœuds a aussi été essayé, puis abandonné : il varie selon le traitement appliqué à chaque version de la carte (lissage de l'altitude, par exemple), et le modèle apprenait la différence entre versions.

### Contraintes issues de la connaissance métier

Certaines connaissances empiriques ont une direction certaine, par exemple pour les alertes turn_direction :

- un chevauchement trop long (`log_overlap_length`) vient d'une route étroite, pas d'un vrai croisement / d'une vraie convergence ;
- les alertes aux intersections avec feux (`traffic_light`) sont surtout des faux positifs ;
- les alertes sans intersection (`isolated`, par exemple une divergence) sont surtout des faux positifs.

Ces caractéristiques sont listées dans `NEGATIVE_FEATURES` de [utils/features.py](utils/features.py), et leur coefficient est contraint à ≤ 0 pendant l'entraînement : **l'expérience fixe la direction, les données fixent l'ampleur**. Si les données ne le confirment pas, le coefficient reste à 0 (`train_model.py` affiche `at the bound`), donc sans effet, mais il ne peut jamais augmenter p. Le `LogisticRegression` de sklearn ne permet pas de contraindre les coefficients ; l'entraînement (`utils/tranning.py`) résout donc la même fonction objectif avec L-BFGS-B de scipy, ce qui donne le même résultat que sklearn en l'absence de contrainte.

Il y a trois façons de transmettre une connaissance au modèle, avec des effets différents :

| Méthode | Mise en œuvre | Effet |
|---|---|---|
| Ajouter une caractéristique | Ajouter dans `FEATURES` une caractéristique qui décrit la situation | Les données fixent direction et ampleur ; instable avec peu d'échantillons |
| Contraindre le signe du coefficient | L'ajouter à `NEGATIVE_FEATURES` | Direction fixe, les données fixent l'ampleur ; sans effet si les données ne la confirment pas |
| Modifier les labels | Écrire ces alertes dans le fichier ignore du dépôt correspondant (`ignore_file` dans `config/dataset.yaml`) pour qu'elles deviennent label 0 | Le modèle l'apprendra forcément ; mais si toutes ces alertes sont des faux positifs, il est plus simple d'en faire directement une règle |

Pour l'instant, `traffic_light` n'a que 14 échantillons d'entraînement (dont 3 corrigés) et l'ampleur de la baisse n'est pas fiable ; pour la fiabiliser, on peut écrire dans les fichiers ignore les alertes d'intersections à feux confirmées inutiles dans chaque carte, puis réentraîner.

### Performances actuelles (2026-10-15, 10 dépôts, 256 versions de carte)

L'évaluation se fait en leave-one-map-out : à chaque fois, un dépôt est mis de côté, le modèle est entraîné sur les autres puis note ce dépôt, ce qui équivaut aux performances sur une nouvelle carte. L'AUC est la probabilité qu'en tirant au hasard un vrai problème et un faux positif, le vrai problème ait le score le plus élevé ; 0.5 équivaut au hasard.

| Vérification | Échantillons d'entraînement | AUC du modèle | Meilleure AUC d'une caractéristique seule |
|---|---|---|---|
| turn_direction | 632 (218 corrigés) | 0.77 | 0.72 (`isolated`) |
| right_of_way | 359 (105 corrigés) | 0.72 | 0.62 |

`train_model.py` affiche les résultats par dépôt et les coefficients du modèle ; après un nouvel entraînement, c'est sa sortie qui fait foi.

Ce que le modèle a appris :

- **turn_direction** :
  - Les alertes sans intersection (`isolated`) sont les moins souvent corrigées ; c'est la caractéristique la plus utile. Après avoir exclu les contacts de bord des croisements, l'AUC du modèle est passée de 0.70 à 0.77.
  - Plus une lanelet croise d'autres lanelets, moins elle est corrigée : l'équipe ne marque généralement pas `straight` les lanelets droites qui intersectent.
  - Plus le chevauchement est long, moins l'alerte est corrigée : dans buc et cern, plus de la moitié des alertes non corrigées ont un chevauchement de plus de 15 m, contre seulement 2 à 11 % des alertes corrigées ; à 30 m de chevauchement, l'odds vaut environ 0.77 fois celui à 5 m.
  - Aux intersections avec feux, l'odds vaut environ 0.26 fois celui des autres (p moyen de 0.41 à 0.19), mais sur seulement 14 échantillons.
- **right_of_way** : plus la surface de chevauchement est grande, plus l'alerte est corrigée ; la plupart des très petits chevauchements ne sont pas traités.

### Limites

- Le label signifie « l'équipe cartographie l'a-t-elle corrigée par la suite » : le modèle apprend les habitudes de l'équipe, pas ce dont Autoware a besoin. Par exemple, Autoware exige un `turn_direction` sur les lanelets droites en conflit dans le graphe de routage, mais le modèle donne à ces alertes un score plutôt bas.
- Seuls 3 dépôts permettent d'évaluer right_of_way, les résultats sont instables.
- Dans une carte en cours de modification (comme Noisy-le-Roi), une alerte « jamais corrigée » peut simplement ne pas encore l'avoir été, et le label 0 n'est pas fiable ; l'AUC turn_direction de ce dépôt n'est que de 0.56. C'est aussi le dépôt qui a le plus d'échantillons (278), ce qui dégrade les performances sur les autres cartes.
- Quand une régularité découverte est assez nette, mieux vaut la transformer en règle ou en seuil que dépendre indéfiniment du modèle. Par exemple les voies parallèles : le modèle right_of_way n'a aucune caractéristique qui les décrit, et ces longs chevauchements, de grande surface, obtenaient au contraire un p élevé ; ils sont désormais exclus directement par `parallel_min_length` / `parallel_min_gap`, les contacts de bord des virages de même sens restant confiés au modèle.

## Extensions (`ext/`)

Chaque sous-répertoire de `ext/` est une extension indépendante, avec son propre script (`script/`), sa configuration (`config/config.yaml`) et son README.md. Les extensions qui ont `enable: true` dans leur configuration s'exécutent après les vérifications 1 à 3 ; leurs résultats sont écrits dans la même sortie et le même log, et comptent dans le Summary et le code de sortie ; la liste d'ignorés, l'AOI et la carte de chaleur sont traitées comme pour les vérifications 1 à 3. `--ext` / `--only-ext` / `--no-ext` choisissent les extensions à exécuter, `--set` remplace leurs paramètres.

| Extension | Contenu |
|---|---|
| [altitude](ext/altitude/README.md) | Points anormaux de l'altitude `ele` sur les bordures de voie (sauts, pics, dévers excessif, absence de `ele`) ; génère aussi dans le répertoire des logs un graphique d'évolution de l'altitude `<nom du log>_altitude.png` |
| [border_sharing](ext/border_sharing/README.md) | Des voies côte à côte partagent-elles la bordure entre elles ; WARNING si les deux bordures coïncident mais sont des ways différents (pas de changement de voie possible, interstice ou chevauchement entre voies opposées) |
| [crosswalk](ext/crosswalk/README.md) | De combien un passage piéton dépasse au plus latéralement de la route ; WARNING au-delà de `max_extension` |
| [stopline](ext/stopline/README.md) | Une ligne d'arrêt est-elle formée de deux points seulement (une droite et non une ligne brisée) ; WARNING au-delà de 2 points |

**Ajouter une extension** : copier le modèle `cp -r ext/_template ext/<nom>` et écrire une seule fonction `check(ctx, cfg, out)` ; la lecture de la carte, la configuration, la liste d'ignorés, le log et la carte de chaleur sont gérés par le framework. `ctx` contient toutes les informations du fichier de carte (tags et géométrie de tous les nœuds, ways et relations) et les résultats de calcul partagés (chevauchements de lanelets, relations prédécesseur / successeur, etc.) : pas besoin de modifier le code de lecture de la carte ni de refaire les calculs. Étapes et interface dans [ext/README.md](ext/README.md). `python3 -m unittest discover -s tests` charge automatiquement chaque extension de `ext/` et l'exécute une fois sur une carte synthétique.

## Arborescence

```
MapChecker/
  check_lanelet_map.py        point d'entrée
  train_model.py              génère les données d'entraînement et entraîne la régression logistique, produit config/model.yaml
  install.sh                  installe les dépendances (sauf ROS / Autoware)
  README.md                   ce document (version chinoise : doc/README.zh.md)
  doc/
    README.zh.md              ce document en chinois
    CHANGELOG.md              journal des modifications (CHANGELOG.fr.md en français)
    checkinglist.md           liste détaillée des vérifications et des conditions d'alerte (checkinglist.fr.md en français)
  ext/                        extensions (exécutées si enable vaut true dans leur configuration), chacune avec son README.md
    README.md                 comment écrire une extension (interface, ctx, out)
    _template/                modèle pour une nouvelle extension (jamais exécuté automatiquement)
    altitude/                 points anormaux de l'altitude des voies, et graphique d'évolution de l'altitude
    border_sharing/           des voies côte à côte partagent-elles leur bordure
    crosswalk/                de combien un passage piéton dépasse au plus latéralement de la route
    stopline/                 une ligne d'arrêt est-elle une droite de deux points
  tests/                      tests (python3 -m unittest discover -s tests)
  config/
    config.yaml               configuration des vérifications
    ignore.yaml               lanelets ignorées manuellement
    dataset.yaml              configuration de train_model.py (liste des dépôts de cartes, règles d'étiquetage)
    model.yaml                modèle de notation des alertes (généré par train_model.py)
  utils/
    lanelet_map.py            lecture des cartes Lanelet2 OSM (conserve tous les nœuds, ways, relations et MetaInfo)
    context.py                MapContext : toutes les informations de la carte et les calculs partagés par les vérifications (chevauchements, topologie, AOI)
    checks.py                 déroulement des vérifications 1 à 3
    geometry.py               outils géométriques (ligne centrale, variation de cap, surface de chevauchement, AOI)
    topology.py               relations prédécesseur / successeur et chevauchements de lanelets
    check_turn_direction.py   vérification 1
    check_right_of_way.py     vérification 2
    check_autoware.py         rapport de la vérification 3
    validate_autoware_map.py  vérification Autoware (nécessite ROS), exécutable seule
    ext.py                    découverte, chargement et exécution des extensions
    features.py               caractéristiques des alertes
    model.py                  lecture du modèle et notation
    ignore.py                 liste d'ignorés
    config.py                 lecture de config.yaml
    printer.py                sortie terminal / log
    heatmap.py                carte de chaleur et zones à forte concentration de problèmes
    build_dataset.py          génération des données d'entraînement à partir de l'historique git des dépôts de cartes
    tranning.py               entraînement de la régression logistique
  libs/                       bibliothèques tierces installées par install.sh (non versionnées)
  data/                       données d'entraînement (non versionnées)
  logs/                       logs et cartes de chaleur (non versionnés)
```
