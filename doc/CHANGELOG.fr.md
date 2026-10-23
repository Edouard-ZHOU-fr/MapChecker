# Journal des modifications

[中文](CHANGELOG.md) | Français

Ce fichier recense les principales évolutions de chaque version de MapChecker, la plus récente en premier. Le format s'inspire de [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/). Les numéros de version correspondent aux tags git du dépôt. Les changements marqués **Incompatible** imposent de modifier l'utilisation, la configuration ou le code des extensions.

## [Non publié]

### Ajouté

- **Extension `border_sharing`** : vérifie que des lanelets véhicule côte à côte partagent la bordure entre elles. Si les deux bordures en vis-à-vis coïncident (distance ≤ `max_distance`, sur ≥ `min_fraction` de la plus courte) mais sont des ways différents, WARNING avec l'interstice ou le chevauchement entre les voies : dans le même sens, elles ne sont pas adjacentes dans le graphe de routage et le changement de voie est impossible ; en sens opposés, elles devraient partager une seule ligne centrale. Des voies opposées dessinées chacune avec sa propre ligne centrale et qui se chevauchent sont justement à l'origine d'une partie des « crossing overlap without right_of_way » de la vérification 2 (par exemple 5353/9684 dans cern). Voir [ext/border_sharing/README.md](../ext/border_sharing/README.md).
- **Navigation par la légende dans la fenêtre de la carte de chaleur** : un clic sur une entrée de la légende (une vérification, `ERROR` ou `hotspot`) déplace la vue sur le problème suivant de cette catégorie et affiche ses informations ; chaque clic passe au suivant, en boucle ; le bouton précédent de la barre d'outils revient à la vue précédente.
- **Extension `stopline`** : vérifie qu'une ligne d'arrêt (way avec `type=stop_line`) n'est formée que de deux points ; au-delà de 2 points (ligne brisée), WARNING avec la distance du point intermédiaire à la droite reliant les deux extrémités ; moins de 2 points ou nœud inexistant : ERROR. Voir [ext/stopline/README.md](../ext/stopline/README.md).
- **Extension `altitude`** : détecte les points anormaux de l'altitude `ele` sur les bordures de voie (sauts, pics, dévers excessif, absence de `ele`) et génère dans le répertoire des logs un graphique d'évolution de l'altitude `<nom du log>_altitude.png`, voir [ext/altitude/README.md](../ext/altitude/README.md).
- **Fenêtre interactive d'altitude** : avec `isShow: true` dans la configuration, une fenêtre indépendante montrant l'altitude en vue de dessus s'ouvre à la fin du rapport ; un clic sur un point anormal ou sur une lanelet affiche son profil en long et ses détails dans une seconde petite fenêtre.
- **Toutes les informations de la carte** : `ctx.nodes` / `ctx.ways` / `ctx.relations` / `ctx.map` donnent accès à tous les nœuds, ways et relations du fichier de carte (tous les tags, attributs XML et membres) ainsi qu'à `MetaInfo` ; une nouvelle extension n'a pas besoin de modifier le code de lecture de la carte, voir [ext/README.md](../ext/README.md).
- **Fichiers de sortie et fenêtres des extensions** : `ctx.output_prefix` est le préfixe de chemin du log, les fichiers générés par une extension portent le même nom que le log ; les fenêtres qu'une extension crée avec pyplot s'ouvrent à la fin du rapport, en même temps que celle de la carte de chaleur.

### Modifié

- Carte de chaleur : les ERROR de toutes les vérifications (extensions comprises) sont marquées par des points rouges, dessinés au-dessus des points de WARNING, avec leur nombre dans la légende.
- Le README est désormais en français par défaut (`README.md`), la version chinoise est déplacée dans [doc/README.zh.md](README.zh.md).
- **Les voies parallèles ne comptent pas comme chevauchement** (`parallel_min_length` 10 m par défaut, `parallel_min_gap` 1.5 m par défaut, en ligne de commande `--parallel-min-length` / `--parallel-min-gap`) : deux lanelets toutes deux droites, ou de sens opposés (par exemple les deux sens d'une route dans un virage), qui se chevauchent sur une longueur ≥ `parallel_min_length` et dont les lignes centrales restent à ≥ `parallel_min_gap` l'une de l'autre le long du chevauchement ne comptent plus comme chevauchement. Les voies qui se chevauchent le long d'une route étroite ou qui se rétrécit (par exemple des voies opposées dessinées trop larges) étaient auparavant signalées par la vérification 2 comme « crossing overlap without right_of_way », avec un p plutôt élevé à cause de leur grande surface, et par la vérification 1 comme « intersects other lanes » ; elles ne sont plus signalées par aucune des deux. Les convergences / croisements dont les lignes centrales se rejoignent, ainsi que les virages de même sens qui longent une voie voisine, ne sont pas concernés. Le modèle de notation a été réentraîné avec la nouvelle règle.
- Les lanelets à la structure incomplète (pas exactement une bordure gauche et une bordure droite, ou référence à un way / nœud inexistant) étaient auparavant ignorées silencieusement ou faisaient planter le programme ; elles sont désormais listées au début du rapport : `Lanelets not loaded (incomplete, not checked): ...`.
- **Incompatible** : `utils.lanelet_map.load_map()` renvoie un `LaneletMap` (auparavant `(lanelets, reg_elems, aois)`), et les paramètres de `MapContext(lanelet_map, opts, ...)` changent en conséquence ; `utils.heatmap.show_heatmap()` est renommée `show_windows()`. Les vérifications et extensions qui n'accèdent à la carte que via `ctx` ne sont pas concernées.

### Corrigé

- Dans la liste des éléments ignorés en fin de rapport, un id qui n'est pas une lanelet mais qui a bien masqué des problèmes (par exemple le way d'une ligne d'arrêt pour stopline) est affiché `way <id> ... N issue(s) suppressed`, et non plus `not found in map`.
- Carte de chaleur : un problème sans coordonnées était placé à la position moyenne des milieux des lanelets concernées, ce qui tombait dans un endroit sans rapport lorsqu'elles sont très éloignées, par exemple au milieu de la carte pour un right_of_way qui référence une lanelet d'une autre intersection. Au-delà de `heatmap.radius`, la position de la première lanelet est désormais utilisée ; le « do not overlap at all » de la vérification 2 est placé sur la lanelet qui référence l'élément de régulation.

## [3.0.0] - 2026-10-21

Nouveau framework de vérifications par extension : il suffit de créer un répertoire sous `ext/` pour ajouter une vérification.

### Ajouté

- **Extensions `ext/<nom>/`** : chaque extension a son propre script, sa propre configuration et son README.md.
  - Les extensions avec `enable: true` dans leur configuration s'exécutent après les vérifications 1 à 3.
  - Leurs résultats sont écrits dans la même sortie et le même log, et comptent dans le Summary et le code de sortie.
  - La liste d'ignorés, l'AOI et la carte de chaleur sont traitées comme pour les vérifications 1 à 3.
  - Pour en écrire une, voir [ext/README.md](../ext/README.md).
- **Extension `crosswalk`** : mesure de combien chaque extrémité d'un passage piéton dépasse latéralement de la route, et émet un WARNING au-delà de `max_extension`, voir [ext/crosswalk/README.md](../ext/crosswalk/README.md).
- **Modèle `ext/_template/`** : à copier pour créer une extension. C'est aussi un exemple exécutable (il vérifie les tags obligatoires des lanelets véhicule), qui ne s'exécute pas automatiquement.
- **Interface des extensions** : une extension n'a besoin que d'une fonction `check(ctx, cfg, out)`.
  - `ctx` (`MapContext` dans [utils/context.py](../utils/context.py)) fournit la carte ainsi que des résultats de calcul partagés : chevauchements de lanelets, relations prédécesseur / successeur, filtrage AOI, présélection spatiale, etc. Ces résultats sont communs avec les vérifications 1 et 2 et ne sont calculés qu'une fois.
  - Les alertes émises via `out` sont automatiquement rattachées à cette vérification.
  - Un `DEFAULT_CONFIG` optionnel donne les valeurs par défaut des paramètres ; s'il est défini, un nom de paramètre mal orthographié dans la configuration provoque une erreur.
  - Une fonction optionnelle `configure(cfg)` permet de valider les paramètres.
  - Les modules auxiliaires de `script/` peuvent être importés en relatif.
- **Options de ligne de commande** :

  | Option | Effet |
  |---|---|
  | `--ext NAME[,NAME...]` | Exécute les extensions indiquées, quelle que soit la valeur de `enable` |
  | `--only-ext NAME[,NAME...]` | N'exécute que les extensions indiquées, sans les vérifications 1 à 3 |
  | `--no-ext` | N'exécute aucune extension |
  | `--set NAME.KEY=VALUE` | Remplace un paramètre de la configuration d'une extension, répétable |
  | `--list-ext` | Liste toutes les extensions et indique si elles sont activées |

- **Isolation des erreurs** : si une extension lève une exception, `[ERROR] extension <nom> crashed` est affiché avec la traceback, et les autres vérifications se poursuivent normalement.
- **Carte de chaleur** : les problèmes des extensions sont marqués par des points verts, une forme par extension. Ils n'interviennent ni dans la coloration ni dans la détermination des zones, et leur nombre est indiqué à part dans les Hotspots.
- **Liste d'ignorés** : dans `config/ignore.yaml`, le nom d'une extension peut servir de clé pour lister les lanelets ignorées uniquement par cette extension, par exemple `crosswalk: [19942]`.
- **Tests** : `python3 -m unittest discover -s tests` charge automatiquement chaque extension de `ext/` et l'exécute une fois sur une carte synthétique.

### Modifié

- Le déroulement des vérifications 1 à 3 passe de `check_lanelet_map.py` à [utils/checks.py](../utils/checks.py). `utils/build_dataset.py` et `train_model.py` n'importent plus le script d'entrée.
- Dans la section Ignored lanelets en fin de rapport, les entrées des vérifications qui n'ont pas été exécutées sont marquées `check not run`.

### Corrigé

- La liste d'ignorés accepte désormais les id de lanelet négatifs (éléments créés par des éditeurs comme JOSM).
- Une liste d'ignorés écrite `all: 614` (et non une liste) produit maintenant un message d'erreur et le code de sortie 2, au lieu d'un plantage.
- Une liste d'ignorés écrite sous forme de mapping `{614: raison}` ne perd plus la raison.

## [2.1.1] - 2026-10-19

### Ajouté

- **`install.sh`** : installe en une fois toutes les dépendances sauf ROS / Autoware (Ubuntu 22.04).
  - apt installe `python3-pip`, `python3-tk` (pour la fenêtre de la carte de chaleur) et `git`.
  - pip installe numpy, PyYAML, matplotlib, scipy et scikit-learn dans le répertoire `libs/` du projet, sans toucher au système ni au répertoire utilisateur.
  - numpy est limité à `< 2` pour rester compatible avec ROS 2 Humble.
- Au démarrage, `check_lanelet_map.py` et `train_model.py` placent `libs/` en tête de `sys.path`.

## [2.1.0] - 2026-10-19

### Ajouté

- **Fenêtre de la carte de chaleur interactive** :
  - Clic sur un point de problème : affiche le numéro de lanelet, la vérification et le p des problèmes à cet endroit, entoure en orange les lanelets concernées, et affiche dans le terminal les lignes correspondantes du log.
  - Clic ailleurs : affiche le numéro de la lanelet à cet endroit.
  - Le clic fonctionne aussi en mode zoom / déplacement.
- `train_model.py --skip-build` : entraîne directement sur les données existantes de `data/`, sans les régénérer.

### Modifié

- **Incompatible** : `train_model.py` effectue désormais en une seule fois la génération des données d'entraînement et l'entraînement.
  - L'ancien `build_dataset.py` est déplacé dans [utils/build_dataset.py](../utils/build_dataset.py) et ne s'exécute plus seul.
  - La partie entraînement est déplacée dans [utils/tranning.py](../utils/tranning.py).
  - `train_model.py` prend `config/dataset.yaml` avec `-c` ; l'option `--data` est supprimée.
- `sqy_map_planning` est temporairement retiré de `config/dataset.yaml`.

## [2.0.0] - 2026-10-16

### Ajouté

- **Vérification 3 : Autoware** (`autoware_checking: true`, nécessite de sourcer ROS).
  - Exécute [utils/validate_autoware_map.py](../utils/validate_autoware_map.py), version Python de `autoware_lanelet2_validation`.
  - Vérifie les erreurs de chargement de la carte, `format_version`, l'altitude `ele` des nœuds et les feux de circulation.
  - Ses résultats `turn_direction` sont fusionnés dans la vérification 1 ; une même lanelet n'est signalée qu'une fois.
  - Ligne de commande : `--[no-]autoware-checking`.
- **Notation des alertes** (`Using_LR: true`) : une régression logistique attribue un score aux alertes des vérifications 1 et 2 signalant un `turn_direction` / `right_of_way` manquant.
  - Affiche `[WARNING p=…]`, trié par p décroissant. Le Summary indique le nombre d'alertes avec p < 0.5.
  - Le modèle `config/model.yaml` est entraîné par `train_model.py` à partir de l'historique git des dépôts de cartes ; la liste des dépôts et les règles d'étiquetage sont dans `config/dataset.yaml`.
  - La notation n'utilise que les nombres du modèle et ne nécessite pas scikit-learn.
  - Ligne de commande : `--[no-]using-lr`, `--model-file`.
- **Carte de chaleur des problèmes** (`heatmap.enabled: true`) :
  - Liste en fin de rapport les zones à forte concentration de problèmes (Hotspots) et génère un `.png` du même nom dans le répertoire des logs.
  - Pondérée par p lorsque `Using_LR` est activé.
  - Avec `isShow: true`, la fenêtre de la carte de chaleur s'ouvre à la fin du rapport.
  - Ligne de commande : `--[no-]heatmap`, `--[no-]show-heatmap`.
- README.md.

### Modifié

- **Incompatible** : `validate_autoware_map.py` passe de la racine à `utils/` et peut toujours être exécuté seul : `python3 utils/validate_autoware_map.py lanelet2_map.osm`.
- **Incompatible** : avec `only_check_AOI: true` et aucune AOI dans la carte, plus aucune vérification n'est effectuée (auparavant, toute la carte était vérifiée).
- Les réglages par défaut de `config/config.yaml` dans le dépôt deviennent :
  - `only_check_AOI` et `autoware_checking` désactivés ;
  - `heatmap`, `Using_LR` et `isShow` activés.

## [1.0.0] - 2026-10-15

Première version.

### Ajouté

- **`check_lanelet_map.py`** : lit une carte Lanelet2 au format OSM, en coordonnées locales si `local_x` / `local_y` sont présents, sinon en projetant la latitude / longitude. Ne dépend que de numpy et PyYAML.
- **Vérification 1 : turn_direction** :
  - Vérifie que les virages (variation de cap ≥ `turn_angle` sur `turn_window` mètres) ont un `turn_direction`.
  - Vérifie que les lanelets en ligne droite qui croisent d'autres voies ou convergent avec elles ont un `turn_direction`.
  - Options associées : `ignore_isolated_turns`, `straight_as_turn_light`.
- **Vérification 2 : right_of_way** :
  - Vérifie que les paires de lanelets qui se croisent / convergent (surface de chevauchement ≥ `min_overlap_area`) ont un élément de régulation `right_of_way` ; la sortie est groupée par zone (en général une intersection).
  - Vérifie que les `right_of_way` existants sont référencés par la lanelet prioritaire.
  - Vérifie que les deux lanelets se chevauchent réellement ; émet une ERROR si elles ne se chevauchent pas du tout.
- **`only_check_AOI`** : ne vérifie que les lanelets entièrement situées dans les AOI de la carte.
- **Liste d'ignorés `config/ignore.yaml`** : la portée peut être `all`, `turn_direction` ou `right_of_way`. La fin du rapport indique combien de problèmes chaque entrée a masqués.
- **Configuration et sortie** :
  - Le fichier de configuration est `config/config.yaml` ; les options de ligne de commande sont prioritaires.
  - Sortie colorée dans le terminal, écrite aussi dans le log `logs/map_check_YYYYmmdd_HHMMSS.log`.
  - Code de sortie : `0` aucun problème, `1` des problèmes.
- **`validate_autoware_map.py`** : version Python de `autoware_lanelet2_validation`, nécessite ROS, exécutable seul.

[Non publié]: https://github.com/Edouard-ZHOU-fr/MapChecker/compare/3.0.0...HEAD
[3.0.0]: https://github.com/Edouard-ZHOU-fr/MapChecker/compare/2.1.1...3.0.0
[2.1.1]: https://github.com/Edouard-ZHOU-fr/MapChecker/compare/2.1.0...2.1.1
[2.1.0]: https://github.com/Edouard-ZHOU-fr/MapChecker/compare/2.0.0...2.1.0
[2.0.0]: https://github.com/Edouard-ZHOU-fr/MapChecker/compare/1.0.0...2.0.0
[1.0.0]: https://github.com/Edouard-ZHOU-fr/MapChecker/releases/tag/1.0.0
