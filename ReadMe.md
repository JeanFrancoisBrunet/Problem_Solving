# Problem Solving 🧩

Coach en **résolution de problèmes** pas à pas, piloté depuis **Telegram** (ou le terminal), qui guide l'utilisateur à travers quatre méthodes : **8D**, **A3**, **PDCA** et **Kaizen 6 points (K6pts)**.

Le moteur IA est au choix, commutable à chaud : **Groq** (LLM cloud) ou des **LLM locaux via Ollama** sur Raspberry Pi 5. L'utilisateur peut aussi **envoyer une photo** (poste de travail, pièce défectueuse, tableau…) : elle est analysée au regard de l'étape en cours et jointe au rapport.

Le projet est composé de deux fichiers Python :

- **`problem_solving.py`** — le cœur : méthodes, sessions, appels LLM (texte et vision), analyse de photos, rapport, mode terminal
- **`telegram_bot_problemsolving.py`** — l'interface Telegram : passerelle multi-utilisateurs, réception des photos, envoi du rapport

> ℹ️ Le coach **ne résout pas le problème à la place de l'utilisateur** : il challenge ses réponses (affirmation floue, solution déguisée en cause, cause qui accuse une personne plutôt qu'un processus, objectif non mesurable) et pose une question pour le faire progresser. Il n'invente ni faits, ni chiffres, ni causes.

## Architecture générale

```
┌─────────────────────────────────────────────────────────────┐
│                    problem_solving.py                       │
│                                                             │
│  ┌──────────────┐  ┌───────────────┐  ┌─────────────────┐   │
│  │ Méthodes     │  │ Session       │  │ Coach           │   │
│  │ 8D · A3 ·    │  │ par           │  │ retour par      │   │
│  │ PDCA · K6pts │  │ utilisateur   │  │ étape, questions│   │
│  │ (étapes +    │  │ (JSON, reprise│  │ revue finale    │   │
│  │  consignes)  │  │  possible)    │  │                 │   │
│  └──────────────┘  └───────────────┘  └─────────────────┘   │
│  ┌──────────────┐  ┌───────────────┐  ┌─────────────────┐   │
│  │ LLM texte    │  │ LLM vision    │  │ Rapport         │   │
│  │ Groq/Ollama  │  │ Groq/Ollama   │  │ Markdown        │   │
│  │ repli quota  │  │ (photos)      │  │ (+ photos)      │   │
│  └──────────────┘  └───────────────┘  └─────────────────┘   │
└──────────────────────────────┬──────────────────────────────┘
                               │ importé par
              ┌────────────────┴───────────────────┐
              │   telegram_bot_problemsolving.py   │
              │  (texte, photos, menu, rapport)    │
              └────────────────────────────────────┘
```

## Les méthodes

Les étapes, les attendus, les points à couvrir et les outils reprennent les **fiches de synthèse** de chaque méthode (8D, A3, PDCA, Kaizen 6 points).

| Commande         | Méthode             | Étapes                                                                                                                                                                                                                                                                                          |
|---               |---                  |---                                                                                                                                                                                                                                                                                              |
| `/8d [titre]`    | **8D**              | D1 Décrire le problème (QQOQCCP) · D2 Équipe et pilote · D3 Analyse préliminaire · D4 Actions immédiates de sécurisation · D5 Causes racines (occurrence **et** non-détection : 6M, FTA, 5 Pourquoi) · D6 Actions correctives et préventives · D7 Efficacité et transversalisation · D8 Clôture |
| `/a3 [titre]`    | **A3**              | 1 Contexte et enjeux · 2 Situation actuelle · 3 Objectifs et cible · 4 Causes racines · 5 Contre-mesures · 6 Plan d'actions · 7 Résultats et standardisation · 8 Lessons learned                                                                                                                |
| `/pdca [titre]`  | **PDCA**            | Plan · Do · Check · Act                                                                                                                                                                                                                                                                         |
| `/k6pts [titre]` | **Kaizen 6 points** | K1 Partager le sujet et observer ensemble · K2 Analyser factuellement la situation actuelle · K3 Générer des idées nouvelles (principes, puis solutions) · K4 Construire un plan de test · K5 Tester rapidement sur le terrain · K6 Évaluer et lancer la suite                                  |

`/K6pts`, `/kaizen` et `/k6` sont acceptés comme alias.

Chaque méthode porte une consigne pour le coach. Exemple pour le **K6pts** (d'après *Toyota Kaizen Methods*, Isao Kato et Art Smalley) : on se met d'accord sur le sujet avant de chercher des solutions, on distingue **principes** et **solutions**, et on **teste avant de décider** — le coach signale les solutions avancées trop tôt, les tests sans critères ni durée, et les conclusions fondées sur une première impression.

## Déroulement d'une démarche

### Mode guidé (par défaut) : une question à la fois

1. L'utilisateur lance une méthode : `/8d Retours clients lot 42`
2. Le coach présente l'étape (question centrale, attendu, outils), puis pose **les questions de l'étape une par une**. Exemple en 8D / D1 : identification, écart attendu / réel, puis **QQOQCCP déroulé** (Quoi, Qui, Où, Quand, Comment, Combien, Pourquoi), risque, enjeu — 18 questions. Le **QQOQCCP est le même outil en 8D (D1), en A3 (étape 1) et en Kaizen 6 points (K1)**, avec la même structure : le 8D parle de défaut (produit ou processus), l'A3 et le Kaizen d'un défaut **ou** d'une problématique au sens large. Pour **chaque** question du QQOQCCP, le coach demande d'abord l'**Est** (la réponse), puis le **N'est pas** (ce qui pourrait être concerné mais ne l'est pas, pour préciser la réponse) ; le N'est pas peut rester vide (`-`) et n'est jamais relancé par l'IA. Les étapes comptent de 1 à 18 questions (45 en 8D, 41 en A3, 17 en PDCA, 34 en K6pts) ; l'étape **D5 du 8D** suit un déroulé spécifique (voir ci-dessous).
3. Après chaque réponse floue, le coach pose **au plus une relance** ; la réponse à la relance s'ajoute à la précédente et on passe à la question suivante : **le dialogue ne boucle pas**.
4. Après la dernière question : revue brève de l'étape par rapport à l'attendu (verdict *suffisant* / *à compléter*). L'utilisateur peut ajouter un complément libre, puis tape `/ok`.
5. À la dernière étape : **revue de cohérence** par le LLM et **rapport Markdown**.

Les questions sont volontairement **générales**, sans exemple propre à un métier : le programme reste compréhensible par des personnes de tous secteurs, et le coach IA a pour consigne de ne pas donner d'exemples concrets.

**Reprise automatique** : après une interruption (programme ou bot relancé, projet rechargé avec `/charger`), le coach rappelle l'étape et **repose la question en attente** (y compris une relance de l'IA restée sans réponse) : il n'y a rien à taper. Si l'IA est indisponible, votre réponse est conservée et on passe à la question suivante sans relance. `ok`, `valider`, `suivant`… ne sont **jamais enregistrés comme réponse** : ils réaffichent la question en cours (ou passent à la suivante si elle est déjà répondue) ; `oui` et `non` restent des réponses.

Pendant les questions : `-` (ou `/sauter`) laisse une question sans réponse, `/prec` revient en arrière, une phrase avec « ? » interroge le coach sans perdre la question en cours, `oui` / `non` restent de simples réponses.

### Analyse des causes : Ishikawa 6M → FTA → 5 Pourquoi (D5 du 8D, et aussi A3, PDCA, Kaizen 6 points)

C'est le même outil dans toutes les démarches. Il constitue **l'étape D5 du 8D** (occurrence **et** non-détection), **une question de l'étape 4 de l'A3**, **de l'étape P du PDCA** (3ᵉ question) et **de l'étape K2 du Kaizen 6 points** (dernière question), avec l'occurrence seule dans ces trois cas. En A3, PDCA et Kaizen, il est facultatif : `/sanscauses` (ou le bouton « Passer l'analyse des causes ») le passe. La synthèse devient la réponse à cette question.

En modes `guide` et `rapide`, l'étape **D5** se déroule en trois temps, sans appel IA pendant la saisie :

1. **Ishikawa 6M, une cause à la fois** : Machine, Matière, Main-d'œuvre, Méthodes, Milieu, Mesure. Pour chaque facteur, vous écrivez une cause, puis une autre, et `-` (ou `/sauter`, `ok`, `fin`) passe au facteur suivant. Les causes sont numérotées **O1.1, O1.2… O6.n** pour l'**occurrence**, puis le même travail est fait pour la **non-détection** (**N1.1… N6.n**). Le diagramme de chaque Ishikawa est affiché à la fin.
2. **FTA, cause par cause** : un standard existe-t-il ? est-il appliqué ? est-il robuste ? (réponse `oui` / `non`, commentaire possible, ou boutons). **Au premier « non », la cause est retenue comme prouvée** ; trois « oui » l'écartent. `/sauter` laisse une cause non évaluée.
3. **5 Pourquoi en arbre, sur chaque cause prouvée** : une réponse peut avoir plusieurs causes, l'analyse est donc un arbre à plusieurs branches, parcouru en profondeur. Chaque réponse porte un numéro hiérarchique : réponses au 1er pourquoi = **1, 2…** ; leurs enfants = **1.1, 1.2…** ; puis **1.1.1, 1.2.1…** (étiquette complète : `O1.1 › 1.2.1`). Vous répondez « pourquoi ? » de niveau en niveau ; `-` (ou `/fin`, bouton « 🏁 Cause racine atteinte ») termine la branche, et le coach demande alors **une autre réponse au même niveau** (`-` ou « ➡️ Aucune autre réponse » = il n'y en a plus, on remonte d'un niveau). La cause est terminée quand il n'y a plus d'autre réponse au 1er niveau. **Toutes les fins de branche (🎯) sont des causes racines**, chacune fera l'objet d'une action corrective / préventive en D6. Il n'y a pas de nombre imposé : **3 pourquoi peuvent suffire, 5 est un repère, et 6, 7, 8 ou plus sont possibles** : on s'arrête dès que la cause est une cause sur laquelle on peut agir.

`/prec` annule la dernière saisie, à n'importe quel moment. La synthèse (causes racines = fins de branche, arbres des pourquoi, causes prouvées, écartées, non évaluées, puis les deux Ishikawa) est la réponse de l'étape D5 : elle figure dans le rapport et sert de contexte aux étapes suivantes. En mode `libre`, D5 reste une réponse libre.

### Trois modes (`/mode`)

| Mode             | Comportement                                                                                                                              |
|---               |---                                                                                                                                        |
| `guide` (défaut) | Questions une par une + relance IA si la réponse est floue                                                                                |
| `rapide`         | Questions une par une, **sans appel IA** pendant les questions (recommandé avec un modèle local lent)                                     |
| `libre`          | L'utilisateur écrit la réponse de l'étape (ses messages s'additionnent ; `= texte` remplace), le coach commente avec un verdict explicite |

Le mode est mémorisé ; passer de `libre` à `guide` conserve le texte déjà saisi.

### Projets, titre, historique

- **Tout est enregistré après chaque message** : réponses, retours du coach, questions, analyses de photos (`sessions/<id>/<projet>.json`). Un redémarrage ne fait rien perdre, et le projet en cours est repris.
- Plusieurs projets par utilisateur : `/projets` (boutons) puis `/charger <n>` rouvre un projet **avec son historique** et la question en cours ; lancer une nouvelle démarche **conserve** l'ancienne.
- **Les projets vides ne s'accumulent pas** : un projet sans aucune saisie (ni réponse, ni échange, ni photo, ni titre) est supprimé automatiquement au lancement d'une nouvelle démarche, avec `/stop`, et au démarrage du programme ou du bot (le projet ouvert est toujours gardé). Les projets qui contiennent du travail ne sont jamais supprimés automatiquement : `/supprimer <n>`.
- `/titre` s'utilise **à tout moment** (`/titre texte`, ou `/titre` puis le titre au message suivant). Un titre est proposé après la première étape (bouton *Adopter*).
- `/historique [étape|tout]` relit les échanges ; `/rapport complet` les ajoute en annexe du rapport.

### Commandes

| Commande                                               | Description                                                                                   |
|---                                                     |---                                                                                            |
| `/8d` `/a3` `/pdca` `/k6pts` `[titre]`                 | Démarre une démarche                                                                          |
| *(texte libre)*                                        | Réponse à la question en cours (mode guidé)                                                   |
| `-` · `/sauter` · `/prec`                              | Saute la question · revient à la précédente                                                   |
| *phrase avec « ? »*                                    | Question posée au coach (explication générale)                                                |
| `/ok`                                                  | Valide l'étape (`/ok force` s'il reste des questions)                                         |
| `/reponse` · `/effacer`                                | Relit les réponses de l'étape · recommence l'étape                                            |
| `/passer` · `/retour`                                  | Saute l'étape · revient à la précédente (ou rouvre la dernière étape d'une démarche terminée) |
| `/guide`                                               | Rappelle l'étape (attendu, outils) et la question en cours                                    |
| `/oui` · `/non` · `/fin`                               | Analyse des causes : réponse FTA · fin d'un facteur (Ishikawa) ou d'une branche des pourquoi  |
| `/sanscauses`                                          | A3, PDCA, Kaizen : passe l'analyse des causes                                                 |
| `/etat`                                                | Résumé des étapes (✅ faite, 👉 en cours, 📷 photos, 💬 échanges)                             |
| `/titre [texte]` · `/sauver [titre]`                   | Titre · enregistrement explicite                                                              |
| `/projets` · `/charger <n>` · `/supprimer <n>`         | Liste, réouverture, suppression                                                               |
| `/stop`                                                | Met le projet de côté (conservé)                                                              |
| `/historique [étape\|tout]` · `/rapport [complet]`     | Relecture des échanges · rapport                                                              |
| `/modele`                                              | Moteur (Groq / Ollama), modèle texte, modèle photos, mode — **boutons** sous Telegram         |
| `/mode guide\|rapide\|libre` · `/backend groq\|ollama` | Raccourcis                                                                                    |
| `/aide` · `/help`                                      | Menu des commandes : **tableau** en terminal, liste sous Telegram                             |
| `/q` · `/quit`                                         | **Terminal uniquement** : quitte le programme (le projet est enregistré)                      |
| `/photo <chemin> [légende]`                            | **Terminal uniquement** : analyse une image locale                                            |

Sous Telegram, chaque message porte les **boutons** utiles (valider, sauter, état, projets, modèles…) : un bouton exécute simplement la commande correspondante.

### Choix du moteur et des modèles

`/modele` affiche le moteur actif et les modèles disponibles : **Groq** (GPT-OSS 120B, GPT-OSS 20B, Qwen 3.8 27B) ou **Ollama** (modèles réellement installés sur le Pi, détectés automatiquement). Le choix est **mémorisé par utilisateur** ; tant que vous n'avez rien choisi, c'est `BACKEND` du `.env` (défaut `groq`) qui s'applique. Si Groq atteint son quota, le coach bascule seul sur un autre modèle Groq et le signale.

### Terminal : couleurs et fenêtre

Commandes (`/8d`, `/ok`…) en **cyan**, têtes de chapitre et titres du menu en **jaune**, avertissements en rouge ; le texte est réajusté à la largeur réelle de la fenêtre et la durée de chaque réponse IA est affichée. Options : `--no-color` (ou variable `NO_COLOR`), `--no-resize`, `--cols 110 --rows 38`. Le programme demande au terminal de prendre cette taille (110 colonnes × 38 lignes par défaut : la fenêtre n'occupe pas tout l'écran) ; si votre terminal ignore cette demande, lancez-le avec une géométrie, par exemple `lxterminal --geometry=110x38 -e python3 problem_solving.py`.

Au lancement, un **menu court** (l'essentiel, 8 lignes) est affiché ; `/aide` (ou `/help`) ouvre le **menu complet**, en tableau compact réajusté à la largeur de la fenêtre. Tout texte plus haut que la fenêtre (menu, historique, rapport) est présenté **page par page** : Entrée pour la suite, `q` pour arrêter.

**Historique de saisie** : les flèches ↑ / ↓ rappellent vos saisies des sessions précédentes (fichier `.problemSolving_history` dans le dossier du projet, droits 600, 1000 lignes ; autre emplacement avec la variable `PS_HISTORY`).

On quitte avec `/q`, `/quit` (ou Ctrl+C) ; le projet en cours est enregistré.

## 📷 Analyse de photos

Envoyez une photo dans la conversation Telegram (ou une image comme fichier). La **légende** de la photo sert de question.

L'analyse tient compte de la méthode, du problème et de l'étape en cours, et répond en quatre parties courtes :

1. 👁️ **Ce qui est visible** — faits observables uniquement, texte et chiffres lisibles
2. 🔎 **Ce que cela suggère** pour l'étape, formulé comme *hypothèse à vérifier*
3. ⚠️ **Ce qu'on ne peut pas conclure** d'une photo
4. ❓ **Deux vérifications ou mesures** à faire sur le terrain

- L'analyse est **enregistrée avec l'étape** et figure dans le rapport, signalée comme « analyse IA, à vérifier sur le terrain ». Elle est aussi transmise au coach pour ses retours suivants.
- `/ok` exige toujours la **réponse écrite de l'utilisateur** : une photo analysée ne remplace pas ses propres constats.
- **Sans démarche en cours**, le bot propose la méthode la plus adaptée et un énoncé de problème provisoire strictement factuel.
- Les photos sont redimensionnées (1280 px max, rotation EXIF corrigée) avant envoi, ce qui évite les refus pour taille. **Les images ne sont pas conservées** : seule l'analyse l'est.

> 🔒 **Confidentialité** : avec **Groq**, la photo est envoyée au cloud ; avec **Ollama**, elle **reste sur le Pi5**. Pour des postes ou des produits chez un client, préférez `/modele b ollama`, et évitez visages et informations confidentielles.

## 🤖 Moteurs IA

|                | Groq (cloud)                                                                        | Ollama (local, Pi5 - 16 Go RAM)                                                  |
|---             |---                                                                                  |---                                                                               |
| **Texte**      | `openai/gpt-oss-120b` (repli automatique sur `openai/gpt-oss-20b` si quota atteint) | `qwen2.5:7b` (défaut) ou `llama3.2:3b` (plus rapide)                             |
| **Vision**     | `qwen/qwen3.8-27b`                                                                  | `llama3.2-vision:11b` (défaut) ou `moondream`                                    |
| **Atouts**     | Rapide, qualité                                                                     | Données chez soi, aucun quota                                                    |
| **Limites**    | Quota du palier gratuit, données dans le cloud                                      | Plus lent sur Pi5 (réponses en dizaines de secondes, analyse d'image en minutes) |

- **Moondream** ne comprend que l'anglais : le bot lui demande une description factuelle en anglais, puis le modèle texte rédige le retour en français.
- Le palier gratuit de Groq est limité en tokens par minute : le bot patiente brièvement sur un 429 court, puis bascule sur le modèle de repli. Les erreurs sont expliquées à l'utilisateur (quota, service injoignable, modèle introuvable…) avec la commande pour changer de moteur.
- Les modèles se règlent sans toucher au code (voir « Configuration »). Le catalogue Groq évolue : adaptez `GROQ_MODEL` et `GROQ_VISION_MODEL` aux modèles disponibles sur votre compte.

## Prérequis
- Python 3.10+
- Raspberry Pi 5 (ou toute machine Linux ; **Termux** convient pour le terminal ou un bot qui utilise Groq)
- Un bot Telegram créé avec [@BotFather](https://t.me/BotFather) (token)
- Un compte [Groq](https://console.groq.com/) et sa clé API (gratuit), **et/ou** [Ollama](https://ollama.com/) avec les modèles souhaités

## Installation

```bash
git clone https://github.com/JeanFrancoisBrunet/Problem_Solving.git
cd Problem_Solving
pip install requests pillow --break-system-packages
```

Modèles locaux (facultatif) :

```bash
ollama pull qwen2.5:7b
ollama pull llama3.2-vision:11b
ollama pull moondream          # vision rapide, descriptions en anglais
```

Depuis un autre appareil (ex. Termux sur smartphone), pointez `OLLAMA_URL` vers le Pi5 (par exemple via WireGuard) ; Ollama doit alors écouter sur le réseau (`OLLAMA_HOST=0.0.0.0`).

## Configuration

Aucun secret n'est stocké dans le code. Chaque valeur est lue dans les **variables d'environnement**, sinon dans un fichier **`.env`** à côté du script (modèle : `.env.example`, jamais versionné).

| Élément                | Source                                                                                                                                              |
|---                     |---                                                                                                                                                  |
| Token du bot           | `TELEGRAM_TOKEN`, sinon `~/.telegram_config` → `[telegram]` `token_problemsolving`                                                                  |
| Clé Groq               | `GROQ_API_KEY`, sinon `~/Projects/Groq_agent/.groq_config` → `[groq]` `api_key`                                                                     |
| Utilisateurs autorisés | `ALLOWED_IDS` (identifiants Telegram séparés par des virgules) ou `allowed_ids` dans `~/.telegram_config`, **plus** le `chat_id` de ce même fichier |

```ini
# ~/.telegram_config  (chmod 600)
[telegram]
token_problemsolving = VOTRE_TOKEN
chat_id = VOTRE_CHAT_ID
```

| Variable               | Défaut                   | Rôle                                                                                        |
|---                     |---                       |---                                                                                          |
| `BACKEND`              | `groq`                   | Moteur par défaut : `groq` ou `ollama` (le choix fait avec `/modele` prime et est mémorisé) |
| `GROQ_MODEL`           | `openai/gpt-oss-120b`    | Modèle texte Groq                                                                           |
| `GROQ_FALLBACK_MODEL`  | `openai/gpt-oss-20b`     | Repli sur 429 / 413                                                                         |
| `GROQ_VISION_MODEL`    | `qwen/qwen3.8-27b`       | Modèle vision Groq                                                                          |
| `OLLAMA_URL`           | `http://localhost:11434` | Serveur Ollama                                                                              |
| `OLLAMA_MODEL`         | `qwen2.5:7b`             | Modèle texte local                                                                          |
| `OLLAMA_VISION_MODEL`  | `llama3.2-vision:11b`    | Modèle vision local (`moondream` pour le mode rapide)                                       |

### Ouvrir le bot à d'autres personnes
Par défaut, **seul votre `chat_id` est autorisé**. Une personne non autorisée reçoit son identifiant Telegram dans le message de refus : ajoutez-le à `ALLOWED_IDS` et redémarrez le bot. Chaque utilisateur a **sa propre session**. Gardez à l'esprit que chacun consomme votre quota Groq (ou la puissance de votre Pi5).

## Lancement

```bash
# Test sans Telegram, dans le terminal (commande /photo disponible)
python3 problem_solving.py

# Bot Telegram
python3 telegram_bot_problemsolving.py
```

Au démarrage, le bot publie son **menu de commandes** dans Telegram (`/8d`, `/a3`, `/pdca`, `/k6pts`, `/ok`, `/modele`, `/projets`…).

### Démarrage automatique (systemd)

```ini
# /etc/systemd/system/telegram-bot-problemsolving.service
[Unit]
Description=Bot Telegram Problem Solving
After=network-online.target
Wants=network-online.target

[Service]
User=<utilisateur>
WorkingDirectory=/home/<utilisateur>/Projects/Problem_Solving
ExecStart=/usr/bin/python3 /home/<utilisateur>/Projects/Problem_Solving/telegram_bot_problemsolving.py
Restart=on-failure
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now telegram-bot-problemsolving
sudo systemctl status telegram-bot-problemsolving
sudo journalctl -u telegram-bot-problemsolving -f      # suivre les logs
```

## Le rapport

À la fin d'une démarche (ou à tout moment avec `/rapport`), un fichier Markdown est généré dans `rapports/` et renvoyé dans Telegram : une section par étape avec la réponse validée, les analyses de photos signalées comme telles, puis la **revue de cohérence** du LLM (problème bien posé ? causes traitées par les actions ? résultats prouvés ?). Une étape passée avec `/passer` apparaît comme « Non renseigné ».

## Sécurité & robustesse
- **Aucun secret dans le code** ; le token est **masqué** dans les logs et dans les messages d'erreur envoyés au chat
- **Liste blanche d'utilisateurs** par identifiant Telegram ; le bot affiche l'identifiant d'un refusé pour faciliter l'ajout
- `/photo <chemin>` (lecture d'un fichier local) est **réservé au terminal** : un utilisateur Telegram ne peut jamais faire lire un fichier du Pi5
- Photos limitées à 20 Mo à l'entrée, redimensionnées avant envoi au modèle
- Un seul traitement à la fois par utilisateur (verrou par conversation) ; l'indicateur « en train d'écrire » reste actif pendant les analyses longues
- Erreurs LLM jamais enregistrées comme réponse de l'utilisateur : elles sont affichées avec la marche à suivre
- Les sorties de raisonnement (`<think>…</think>`) de certains modèles sont retirées des réponses

## Limites connues
- Une photo ne prouve rien : l'analyse reste une **aide à l'observation**, à confronter au terrain (c'est ce que rappelle chaque analyse)
- La qualité du coach dépend du modèle : les petits modèles locaux (3B) donnent des retours plus généraux
- Plusieurs photos envoyées en album sont analysées **une par une** (consomment le quota en conséquence)
- Les images ne sont pas intégrées au rapport Markdown (seule leur analyse l'est)
- Le coach est en français, au vouvoiement

## Structure du dépôt
```
Problem_Solving/
├── problem_solving.py               # Cœur : méthodes, sessions, LLM texte/vision, rapport, terminal
├── telegram_bot_problemsolving.py   # Interface Telegram
├── .env.example                     # Modèle de configuration
├── .gitignore
└── README.md                        # Ce fichier

# Générés à l'exécution (non versionnés)
├── .env                             # Configuration locale
├── sessions/                        # Une session JSON par utilisateur
└── rapports/                        # Rapports Markdown générés
```

> `sessions/` contient les réponses des utilisateurs et les analyses de photos : ils sont dans le `.gitignore` et ne doivent **jamais** être poussés sur un dépôt public. Vérifiez-le avant le premier `github` / `sync.sh`.

## Auteur
**Jean-François Brunet** — [JFBConseils](https://github.com/JeanFrancoisBrunet)
Consultant Lean Management — projet personnel de coach de résolution de problèmes sur Raspberry Pi 5 — *Octobre 2026*
