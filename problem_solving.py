#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# =============================================================================
#  Problem Solving — coach de résolution de problèmes
#  Méthodes : 8D · A3 · PDCA · Kaizen 6 points
#
#  Cœur du système (méthodes, résolution problèmes, LLM, analyse de photos, mode terminal).
#  L'interface Telegram est dans telegram_bot_problemsolving.py, qui importe ce module.
#
#  - Méthodes : étapes, « attendu », points à couvrir et outils tirés des fiches de synthèse
#    (Synthese_8D / A3 / PDCA / Kaizen6Pts). Le coach s'appuie dessus pour challenger.
#  - Conversation : TOUS les échanges de chaque étape (réponses, retours du coach, questions, photos)
#    sont conservés dans le projet et réinjectés dans le contexte du coach.
#  - Projets : enregistrement automatique à chaque message + /sauver ; plusieurs projets par
#    utilisateur ; /projets et /charger pour les ré-ouvrir avec leur historique ; /titre à tout moment.
#  - Modèles : /modele change de moteur (Groq / Ollama) et de modèle (texte et photos), à chaud ;
#    le choix est mémorisé. Repli automatique Groq sur GROQ_FALLBACK_MODEL (429 / 413).
#  - Boutons : chaque message peut porter des boutons ; un bouton = une commande
#    (utilisés par l'interface Telegram, ignorés en terminal).
#  - Photos : analysées par un modèle vision, dans le contexte de l'étape en cours
#      · Groq   : GROQ_VISION_MODEL   (défaut qwen/qwen3.8-27b)
#      · Ollama : OLLAMA_VISION_MODEL (défaut llama3.2-vision:11b ; avec « moondream » :
#                 description en anglais puis réponse en français par le modèle texte)
#  - Principe : l'outil guide pas à pas, l'utilisateur répond (ses messages s'additionnent),
#    le coach challenge, /ok valide l'étape. À la fin : revue de cohérence + rapport Markdown.
#
#  Dépendances : requests (obligatoire), pillow (recommandé : redimensionne les photos)
#      pip install requests pillow --break-system-packages
#
#  Configuration (variables d'environnement, ou fichier .env à côté du script) :
#    GROQ_API_KEY         sinon lue dans ~/Projects/Groq_agent/.groq_config ([groq] api_key)
#    BACKEND              groq | ollama                      (par défaut : groq)
#    GROQ_MODEL           défaut : openai/gpt-oss-120b
#    GROQ_FALLBACK_MODEL  défaut : openai/gpt-oss-20b (repli sur 429/413)
#    GROQ_VISION_MODEL    défaut : qwen/qwen3.8-27b
#    OLLAMA_URL           défaut : http://localhost:11434
#    OLLAMA_MODEL         défaut : qwen2.5:7b   (llama3.2:3b = plus rapide)
#    OLLAMA_VISION_MODEL  défaut : llama3.2-vision:11b   (ou moondream = très rapide)
#  Les choix faits avec /modele priment sur ces valeurs par défaut (mémorisés par utilisateur).
#
#  Données : sessions/<utilisateur>/<projet>.json (+ _prefs.json) · rapports/*.md
#
#  Test en terminal :  python3 problem_solving.py
#  Splash : icons/ProblemSolving.png s'affiche 2 s au lancement en terminal (si un écran est
#           disponible) ; --no-splash le désactive. Jamais affiché par le bot Telegram.
#
#  Auteur : Jean-François BRUNET – JFBConseils – Octobre 2026
#  Rev.   : 
# =============================================================================

import io
import os
import re
import sys
import json
import time
import base64
import shlex
import argparse
import configparser
from datetime import datetime
from pathlib import Path

import requests

try:
    from PIL import Image, ImageOps
except ImportError:  # Pillow facultatif
    Image = None

try:                      # splash : tkinter et ImageTk facultatifs (Pi sans écran, Termux…)
    import tkinter as tk
except ImportError:
    tk = None
try:
    from PIL import ImageTk
except ImportError:
    ImageTk = None

BASE_DIR = Path(__file__).resolve().parent
ICON_DIR = BASE_DIR / "icons"
SPLASH_IMAGE = "ProblemSolving.png"
SPLASH_MS = 2000
DATA_DIR = BASE_DIR / "sessions"
REPORT_DIR = BASE_DIR / "rapports"
DATA_DIR.mkdir(exist_ok=True)
REPORT_DIR.mkdir(exist_ok=True)

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
def charger_env():
    env_file = BASE_DIR / ".env"
    if env_file.exists():
        for ligne in env_file.read_text(encoding="utf-8").splitlines():
            ligne = ligne.strip()
            if ligne and not ligne.startswith("#") and "=" in ligne:
                k, v = ligne.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

def lire_ini(chemin, section, cle):
    """Lit une valeur dans un fichier .ini (sans interpolation) ; '' si absente."""
    try:
        cp = configparser.RawConfigParser()
        cp.read(Path(chemin).expanduser(), encoding="utf-8")
        return cp.get(section, cle, fallback="").strip()
    except Exception:
        return ""

charger_env()

CFG = {
    "GROQ_API_KEY": os.getenv("GROQ_API_KEY")
    or lire_ini(os.getenv("GROQ_CONFIG", "~/Projects/Groq_agent/.groq_config"), "groq", "api_key"),
    "BACKEND": os.getenv("BACKEND", "groq").lower(),
    "GROQ_MODEL": os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
    "GROQ_FALLBACK_MODEL": os.getenv("GROQ_FALLBACK_MODEL", "openai/gpt-oss-20b"),
    "GROQ_VISION_MODEL": os.getenv("GROQ_VISION_MODEL", "qwen/qwen3.8-27b"),
    "OLLAMA_URL": os.getenv("OLLAMA_URL", "http://localhost:11434").rstrip("/"),
    "OLLAMA_MODEL": os.getenv("OLLAMA_MODEL", "qwen2.5:7b"),
    "OLLAMA_VISION_MODEL": os.getenv("OLLAMA_VISION_MODEL", "llama3.2-vision:11b"),
}
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
IMAGE_MAX_OCTETS = 20 * 1024 * 1024      # photo à l'entrée
IMAGE_MAX_ENVOI = 3_500_000              # après redimensionnement (limite Groq : 4 Mo en base64)

# Budget de contexte (le plan gratuit Groq plafonne à ~8 000 tokens/min par modèle)
CTX_MAX_CHARS = 4000      # étapes déjà validées, résumées dans le prompt
FIL_MAX_CHARS = 2500      # retours du coach / questions déjà échangés sur l'étape
FIL_ENTREE_MAX = 600      # taille max d'une entrée du fil dans le prompt

# Modèles proposés par /modele (Ollama : liste lue dynamiquement sur le Pi)
GROQ_MODELES = [
    ("openai/gpt-oss-120b", "GPT-OSS 120B", "meilleur raisonnement"),
    ("openai/gpt-oss-20b",  "GPT-OSS 20B",  "rapide"),
    ("qwen/qwen3.8-27b",    "Qwen 3.8 27B", "raisonnement + vision"),
]
GROQ_VISION_MODELES = [
    ("qwen/qwen3.8-27b", "Qwen 3.8 27B", "vision"),
]
INDICE_VISION = re.compile(r"vision|llava|moondream|minicpm-v|bakllava|-vl|vl:|gemma3|llama4", re.I)

# ----------------------------------------------------------------------------
# Définition des méthodes — d'après les fiches de synthèse
# ----------------------------------------------------------------------------
def E(code, titre, question, attendu, points, outils):
    """Une étape : question centrale, résultat attendu, points à couvrir, outils associés."""
    return {"code": code, "titre": titre, "question": question, "attendu": attendu,
            "points": points, "outils": outils}

METHODES = {
    "8d": {
        "nom": "8D",
        "principe": ("Démarche 4D/8D : travail d'équipe pluridisciplinaire, communication "
                     "forte, focus sur le processus qui génère le problème. Se lit en 4 temps : "
                     "comprendre (D1-D3), protéger le client (D4), traiter les causes (D5-D6), "
                     "pérenniser (D7-D8)."),
        "note": ("Démarche 8D : la définition du problème (D1) est l'une des étapes les plus "
                 "importantes ; la sécurisation (D4) est temporaire et protège le client à 100 % "
                 "sans traiter les causes ; D5 exige des causes racines prouvées pour l'occurrence ET "
                 "pour la non-détection (6M, FTA, 5 Pourquoi) ; D6 au moins une action par cause racine, "
                 "standardisée ; D7 une preuve que ça tient et la transversalisation."),
        "etapes": [
            E("D1", "Décrire le problème", "Quel est le problème ?",
              "Un problème défini sans ambiguïté, mesuré comme un écart entre l'attendu (standard, "
              "plan, cahier des charges, norme, attente client implicite) et le réel, compris de la "
              "même façon par tous.",
              ["Titre reconnaissable, référence article (avec indice), n° de lot, quantité concernée, "
               "date et/ou n° de litige",
               "Écart attendu / réel, quantifié",
               "QQOQCCP : quoi, qui, où, quand, comment, combien, pourquoi (faits, pas d'opinions), avec "
               "pour chaque question un « Est » (la réponse) et un « N'est pas » (ce qui est exclu, pour "
               "préciser la réponse ; parfois sans objet)",
               "Risque : problème récurrent ? autres produits ou processus touchés ?",
               "Enjeu : risque client, coût, logistique"],
              "QQOQCCP, Est / N'est pas, 7 outils de la Qualité (relevés, Pareto, Ishikawa, SPC…)"),
            E("D2", "Constituer l'équipe et désigner le pilote", "Qui travaille dessus ?",
              "Une équipe pluridisciplinaire, motivée, compétente et disponible, conduite par un "
              "pilote qui garantit la méthode ; active jusqu'à D8.",
              ["Pilote 8D (nom, fonction) ; sponsor / management notifié de l'ouverture et du rythme",
               "Équipe de 3 à 5 membres : terrain, encadrement, experts, éventuellement un « candide », "
               "le client ou le fournisseur",
               "Fonctions à mobiliser (Études, Maintenance, Méthodes, Qualité, Fabrication, Achats, "
               "Logistique, Opérateurs…)",
               "Fonctionnement : objectifs, rôles et responsabilités, communication interne et externe, "
               "lieu et rythme des réunions"],
              "Identification du client, fonctions à mobiliser, charte d'équipe"),
            E("D3", "Analyse préliminaire", "Où est la source ?",
              "Savoir où se situent les causes potentielles d'occurrence ET de non-détection, avant "
              "toute analyse approfondie.",
              ["Nature du problème : sait-on le reconnaître ?",
               "Flux de matières, pièces ou informations reconstitué jusqu'au lieu de détection",
               "Où le problème a pu naître (occurrence) ? Où aurait-il dû être arrêté (non-détection) ?",
               "Problème complexe : faut-il le décomposer (problèmes similaires en des endroits "
               "différents) ?"],
              "Cartographie du flux, Pareto, concentration des défauts, comparaison conforme / non conforme"),
            E("D4", "Actions immédiates de sécurisation", "Comment protéger le client maintenant ?",
              "Le client est protégé à 100 % des effets du problème, en attendant les actions "
              "correctives ; action temporaire qui ne traite pas la cause ; validée par le client ou "
              "son représentant.",
              ["Actions retenues (quarantaine, tri, retouche, rebut…), d'efficacité prouvée, faciles à "
               "mettre en œuvre, au coût maîtrisé ; qui, quand",
               "Tous les services concernés sont-ils informés ?",
               "Un contrôle des effets prouve-t-il que le client sera bien protégé ?",
               "Le client est-il d'accord (son autorisation est-elle nécessaire) ?",
               "Le processus d'escalade (autres unités, Direction) est-il respecté ?"],
              "Quarantaine, tri, retouche, rebuts (mur qualité), contrôle des effets, causes communes vs spéciales"),
            E("D5", "Causes racines (occurrence et non-détection)",
              "Pourquoi est-ce arrivé, et pourquoi n'a-t-il pas été détecté ?",
              "Une cause racine prouvée pour chacun des écarts : pourquoi le problème est apparu "
              "(occurrence) et pourquoi il n'a pas été détecté (non-détection).",
              ["6M (Ishikawa) : facteurs probables : Machine, Matière, Main-d'œuvre, Méthodes, Milieu, "
               "Mesure — qu'est-ce qui a pu générer le problème ?",
               "FTA pour prouver chaque facteur : y a-t-il un standard ? est-il respecté ? est-il robuste ?",
               "5 Pourquoi sur chaque facteur prouvé, jusqu'à la cause racine",
               "Les deux dimensions sont-elles traitées : occurrence & non-détection ?"],
              "6M (Ishikawa), FTA (Factor Tree Analysis), 5 Pourquoi, DOE si besoin"),
            E("D6", "Actions correctives et préventives", "Que faire pour l'éliminer ?",
              "Au moins une action en face de chaque cause racine, validée par le management, qui "
              "résout le problème sans créer d'autre effet néfaste ; standards adaptés et suivis (VRS).",
              ["Nature de chaque cause racine : technique, systémique ou management",
               "Solutions possibles pour chaque cause racine, puis choix selon coût, délai, facilité, "
               "efficacité",
               "Analyse de risques (AMDEC) : pas d'effet néfaste ?",
               "Plan d'actions court terme (quoi, qui, quand) et plan d'amélioration long terme",
               "Standardisation : procédures, instructions, modes opératoires modifiés ; suivi du "
               "respect des standards (VRS)",
               "Validation par le management"],
              "Matrice de sélection des actions, AMDEC, plan d'actions, plan d'amélioration de la qualité"),
            E("D7", "Efficacité des actions et transversalisation", "Ça tient, et on généralise ?",
              "La preuve que la solution tient dans le temps (non-réapparition) et que le retour aux "
              "écarts antérieurs est impossible ; actions préventives étendues aux cas similaires.",
              ["Résultats mesurés : objectifs techniques et économiques atteints, pas de dérive",
               "Robustesse : le problème ne réapparaît pas (quels indicateurs, sur quelle durée ?)",
               "Transversalisation : systèmes, processus ou produits similaires concernés",
               "Standards de management visuel, AMDEC, plan de surveillance, lessons learned mis à jour"],
              "Indicateurs de suivi, cartes de contrôle SPC, standards, lessons learned"),
            E("D8", "Clôture", "A-t-on capitalisé ?",
              "Problème considéré comme résolu : solutions validées, vérifiées et pérennisées, "
              "approuvées par les membres dont l'autorité est requise.",
              ["Effort collectif de l'équipe reconnu ; le pilote remercie puis dissout l'équipe",
               "Information remontée au management",
               "Rapport 8D final diffusé et archivé",
               "Lessons learned versées à la base de connaissances"],
              "Rapport 8D final, lessons learned, revue de clôture"),
        ],
    },
    "a3": {
        "nom": "A3",
        "principe": ("A3 (Toyota) : une logique PDCA rendue visible sur une seule feuille, de gauche "
                     "à droite — comprendre (étapes 1-4), agir (5-6), vérifier et pérenniser (7-8). "
                     "Se construit sur le terrain, avec les équipes ; le coach questionne plus qu'il "
                     "ne corrige."),
        "note": ("A3 (Toyota) : un récit logique du contexte jusqu'aux enseignements ; on va voir sur "
                 "le terrain (gemba) ; chaque contre-mesure est reliée à une cause racine PROUVÉE ; "
                 "l'objectif est mesurable (valeur de départ, cible, échéance) ; les résultats sont "
                 "comparés à l'état initial et à l'objectif avant de standardiser."),
        "etapes": [
            E("1", "Clarifier le contexte et les enjeux", "Pourquoi ce sujet ?",
              "Un sujet pertinent, relié aux attentes du client et aux objectifs de l'entreprise, que "
              "chacun comprend de la même façon.",
              ["Pourquoi ce sujet, et pourquoi maintenant ?",
               "Quel enjeu pour le client, la sécurité, la qualité, les coûts, les délais ?",
               "QQOQCCP : le sujet est-il posé sans formulation vague ? (quoi, qui, où, quand, comment, combien, "
               "pourquoi, chacun avec son « Est » et son « N'est pas »)",
               "Quels indicateurs (KPI) en lien avec la stratégie ?"],
              "QQOQCCP, indicateurs (KPI)"),
            E("2", "Comprendre la situation actuelle", "Où en est-on vraiment ?",
              "Une description factuelle et chiffrée de l'écart entre le standard attendu et le réel, "
              "observée sur le terrain.",
              ["Qu'avez-vous vu sur le lieu même du problème (gemba) ?",
               "Quantification : fréquence, localisation, tendance",
               "Écart entre le standard attendu et le réel",
               "Comparaison conforme / non conforme : qu'est-ce qui change ?"],
              "Feuille de relevés, Pareto, histogramme, cartographie du processus (MIFA)"),
            E("3", "Définir les objectifs et la situation cible", "Que vise-t-on, et pour quand ?",
              "Un objectif mesurable, daté, ambitieux mais réaliste, avec un résultat visé à court "
              "terme et une cible à long terme.",
              ["Objectif exprimé en valeur de départ, valeur cible et échéance",
               "Gain rapide (court terme) distingué de l'ambition durable (long terme)",
               "Indicateurs de résultat et de processus"],
              "Objectifs SMART, indicateurs (KPI)"),
            E("4", "Identifier les causes racines", "Pourquoi est-ce arrivé ?",
              "Une ou plusieurs causes racines prouvées sur le terrain, au-delà des symptômes.",
              ["Causes possibles énumérées et classées (6M)",
               "Chaque cause vérifiée sur le terrain (preuves)",
               "Descente jusqu'à la cause sur laquelle on peut agir (5 Pourquoi)",
               "Liens entre les causes (arbre des causes)"],
              "Diagramme d'Ishikawa (6M), 5 Pourquoi, arbre des causes, Pareto"),
            E("5", "Définir les contre-mesures", "Que fait-on contre chaque cause ?",
              "Au moins une contre-mesure par cause racine, choisie sur des critères explicites et "
              "validée avec les équipes concernées.",
              ["Plusieurs solutions envisagées, puis retenue des plus efficaces et des plus simples",
               "Critères de choix : coût, délai, efficacité (impact / effort)",
               "Effet attendu de chaque contre-mesure sur sa cause racine",
               "Impact sur les autres équipes et sur le client"],
              "Matrice impact / effort, critères coût-délai-efficacité"),
            E("6", "Établir le plan d'actions", "Qui fait quoi, quand ?",
              "Un plan clair : qui fait quoi, pour quand, avec quel suivi.",
              ["Un responsable et une échéance par action",
               "Jalons et suivi (management visuel)",
               "Une action ou un changement à la fois, pour comprendre les impacts"],
              "Plan d'actions (Qui, Quoi, Quand), planning (Gantt), management visuel"),
            E("7", "Mesurer les résultats et standardiser", "Ça marche, et ça tient ?",
              "La preuve que l'objectif est atteint, puis la fixation de la nouvelle façon de faire "
              "pour éviter le retour en arrière.",
              ["Situation comparée à l'état initial & à l'objectif (indicateurs avant / après)",
               "Aucun effet négatif ailleurs",
               "Résultats satisfaisants → standard de travail et procédures mis à jour ; "
               "sinon ajustement et nouveau cycle"],
              "KPI avant / après, carte de contrôle, standards de travail, procédures"),
            E("8", "Capitaliser : lessons learned", "Qu'en retient-on ?",
              "Les enseignements sont formalisés et partagés pour être réutilisés ailleurs.",
              ["Qu'a-t-on appris sur le produit, le processus, notre façon de travailler ?",
               "Qui d'autre peut en profiter (transversalisation aux sujets similaires) ?",
               "Quel nouveau sujet ouvre-t-on ?"],
              "Fiche lessons learned, partage en réunion d'équipe"),
        ],
    },
    "pdca": {
        "nom": "PDCA",
        "principe": ("PDCA (roue de Deming) : cycle d'amélioration continue en 4 étapes ; on traite un "
                     "problème à la fois, on apprend de chaque contexte et la roue reste en "
                     "mouvement. Bon pour les problèmes clients ou issus des andons opérateurs."),
        "note": ("PDCA : Plan = problème bien posé + causes hypothétiques CONFIRMÉES sur le terrain + "
                 "contre-mesure envisagée avec les équipes ; Do = un seul changement à la fois ; "
                 "Check = comparaison avant / après sur les mêmes indicateurs, sans effet négatif "
                 "ailleurs ; Act = corriger, abandonner ou standardiser, puis ouvrir un nouveau cycle."),
        "etapes": [
            E("P", "Plan — Planifier", "Quel est le bon sujet, et pourquoi ?",
              "Un problème bien posé, des causes hypothétiques confirmées sur le terrain, et une "
              "contre-mesure envisagée avec les équipes.",
              ["Quel problème ou écart veut-on corriger ? Quel changement ? Est-ce le bon sujet pour "
               "le client, pour l'opérateur ?",
               "Hypothèses de causes formulées, puis analysées",
               "Hypothèses confirmées directement sur le terrain (gemba)",
               "Contre-mesure envisagée avec les équipes",
               "Impact anticipé sur les autres équipes et sur le client"],
              "Diagramme d'Ishikawa, 5 Pourquoi, confirmation sur le terrain (gemba)"),
            E("D", "Do — Faire/Réaliser", "Que change-t-on, un seul changement à la fois ?",
              "Un changement testé, dont on comprend les effets.",
              ["Quelle action ou quel changement a réellement été engagé ?",
               "Un seul changement à la fois, pour comprendre les impacts ?",
               "Écarts par rapport au plan d'actions"],
              "Plan d'actions de la contre-mesure retenue"),
            E("C", "Check — Vérifier", "Ça a marché, sans effet négatif ailleurs ?",
              "La preuve que l'action produit l'effet voulu, sans impact négatif ailleurs ; écarts "
              "analysés entre la situation initiale et la nouvelle.",
              ["Impact de l'action ou du changement vérifié (mesures)",
               "Comparaison nouvelle situation / situation initiale sur les mêmes indicateurs",
               "Absence d'effet négatif ailleurs",
               "Analyse des écarts"],
              "Comparaison avant / après sur les mêmes indicateurs"),
            E("A", "Act — Ajuster", "Qu'en retient-on ?",
              "Des décisions claires : corriger, abandonner ou standardiser, et l'ouverture d'un "
              "nouveau cycle.",
              ["Que faut-il corriger ? Qu'est-ce qui n'a pas marché, quelle erreur de raisonnement ?",
               "Que doit-on abandonner ?",
               "Qu'a-t-on appris sur le produit, le processus ou de notre geste ? Qui cela peut-il intéresser ?",
               "Que peut-on standardiser si les résultats sont satisfaisants ?",
               "Quel nouveau cycle PDCA lancer maintenant ?"],
              "Standardisation, partage des enseignements"),
        ],
    },
    "k6pts": {
        "nom": "Kaizen 6 points",
        "principe": ("Kaizen 6 points : une discipline quotidienne pour améliorer le travail réel : "
                     "on observe ensemble, on teste vite, on retient ce qui marche, "
                     "puis on passe au sujet suivant. Se lit en 3 temps : "
                     "comprendre (1-2), imaginer et tester (3-5), intégrer et relancer (6)."),
        "note": ("Kaizen 6 points : on part du travail réel sur le terrain, on se met d'accord sur le "
                 "sujet avant de chercher des solutions, on distingue les principes d'amélioration des "
                 "solutions, on construit un plan de test (quoi, durée, mesures, critères) et on teste "
                 "avant de décider : on valide un résultat terrain, pas une opinion. Signale les "
                 "solutions avancées trop tôt, les tests sans critères ni durée, et les conclusions "
                 "fondées sur une première impression."),
        "etapes": [
            E("K1", "Partager le sujet et observer ensemble", "Sommes-nous d'accord sur le problème ?",
              "Un accord de l'équipe sur le problème à résoudre, avant que chacun n'arrive avec sa "
              "solution.",
              ["Sommes-nous d'accord sur le problème à résoudre ?",
               "Observation collective au poste (gemba) : QQOQCCP (quoi, qui, où, quand, comment, combien, "
               "pourquoi), chacun avec son « Est » et son « N'est pas »",
               "(Évitez d'arriver avec une solution.)"],
              "Observation collective au poste (gemba), QQOQCCP"),
            E("K2", "Analyser factuellement la situation actuelle", "Que se passe-t-il vraiment ?",
              "Des faits à la place des opinions, qui rendent le travail réel visible et confirment "
              "qu'il existe une opportunité d'amélioration.",
              ["Observation directe et relevés terrain (mesures, temps…)",
               "Description détaillée du travail réel : sur quoi agit-on exactement ?",
               "Schémas, plans, diagramme spaghetti (une photo du poste peut aider)",
               "Y a-t-il bien une opportunité d'amélioration ?"],
              "Observation directe, relevés, schémas, diagramme spaghetti"),
            E("K3", "Générer des idées nouvelles", "Quels principes pourraient répondre au sujet ?",
              "Un ensemble de principes différents qui répondent au sujet, sans choisir de solution "
              "trop vite.",
              ["Quels PRINCIPES d'amélioration (idées générales qui répondent au sujet) ?",
               "Pour chaque principe, quelles solutions possibles (moyens concrets pour l'appliquer) ?",
               "On ne choisit pas encore"],
              "Brainstorming d'équipe par principes, puis par solutions"),
            E("K4", "Construire un plan de test", "Comment saura-t-on que ça marche ?",
              "Un plan qui permet de tester avant de décider, et d'éviter de s'arrêter à une première "
              "impression positive ou négative.",
              ["Ce qui sera testé",
               "Pendant combien de temps",
               "Avec quelles mesures",
               "Selon quels critères de réussite ; qui teste, et où ?"],
              "Plan de test : objet, durée, mesures, critères de réussite"),
            E("K5", "Tester rapidement sur le terrain", "Qu'est-ce qui marche vraiment ?",
              "Un résultat terrain, pas une opinion : les idées sont testées immédiatement dans le "
              "travail réel.",
              ["Ce qui marche, ce qui ne marche pas",
               "Les variables qui influencent le résultat",
               "L'idée réellement efficace (résultat mesuré selon le plan de test)"],
              "Essai en situation réelle au poste, mesures du plan de test"),
            E("K6", "Évaluer et lancer la suite", "Qu'intègre-t-on, et quel sujet ensuite ?",
              "La meilleure idée, validée par les équipes, est intégrée puis déployée, et un nouveau "
              "sujet d'amélioration est ouvert.",
              ["L'idée retenue est-elle validée par l'équipe ?",
               "Intégration au standard, aux règles de sécurité, aux pratiques de production",
               "Déploiement plus large (postes similaires)",
               "Quel sera le prochain sujet d'amélioration ?"],
              "Standards et règles de sécurité mis à jour, déploiement aux postes similaires"),
        ],
    },
}
ALIAS_METHODES = {"k6": "k6pts", "kaizen": "k6pts", "k6points": "k6pts"}

# ----------------------------------------------------------------------------
# Questions posées une à une dans chaque étape (mode guidé) — d'après les 
# « points à couvrir » des synthèses. Q(libellé du rapport, question).
# ----------------------------------------------------------------------------
def Q(libelle, question, exemple=""):
    return (libelle, question, exemple)

# QQOQCCP avec « Est / N'est pas » : un seul outil, la même structure dans les trois démarches.
# Pour chaque question : l'Est (la réponse), puis le N'est pas (ce qui est exclu, pour préciser la problématique; parfois vide).
# QQOQCCP : version « défaut » (produit ou processus), pour le 8D (D1).
# QQOQCCP_GENERAL : même structure, vocabulaire plus large (défaut OU problématique), pour l'A3 et le Kaizen.
QQOQCCP = [
    Q("QUOI (Est)", "Quel est précisément le défaut ou le dysfonctionnement ? Qu'est-ce qui est "
      "touché (produit, pièce, machine, processus) ?"),
    Q("QUOI (N'est pas)", "Qu'est-ce qui pourrait être touché mais ne l'est pas "
      "(autres défauts, autres produits ou éléments, ce qui est conforme) ? « - » s'il n'y a rien à exclure."),
    Q("QUI (Est)", "Qui a détecté le problème ? Qui est concerné (poste, équipe, client) ?"),
    Q("QUI (N'est pas)", "Qui pourrait être concerné mais ne l'est pas (autres postes, "
      "équipes, clients) ? « - » s'il n'y a rien à exclure."),
    Q("OÙ (Est)", "À quel endroit apparaît-il (site, ligne, machine, poste), et où est-il détecté ? "
      "Où est-il visible sur la pièce ?"),
    Q("OÙ (N'est pas)", "Où pourrait-il apparaître mais n'apparaît pas (autres sites, "
      "lignes, machines, postes, autres zones de la pièce) ? « - » s'il n'y a rien à exclure."),
    Q("QUAND (Est)", "Quand est-il apparu la première fois, depuis quand, à quelle fréquence ? "
      "Un moment particulier (équipe, changement de lot, démarrage) ?"),
    Q("QUAND (N'est pas)", "Quand n'apparaît-il pas (périodes, équipes, lots ou "
      "moments sans le problème) ? « - » s'il n'y a rien à exclure."),
    Q("COMMENT (Est)", "Comment se manifeste-t-il, et comment a-t-il été détecté "
      "(contrôle, mesure, réclamation) ?"),
    Q("COMMENT (N'est pas)", "Comment ne se manifeste-t-il pas (autres formes du "
      "problème non observées, autres moyens de détection sans effet) ? « - » s'il n'y a rien à exclure."),
    Q("COMBIEN (Est)", "Quelle quantité, quel taux, quel coût ? (nombre de pièces, %, lots "
      "concernés)"),
    Q("COMBIEN (N'est pas)", "Quelle quantité ou quels lots ne sont pas concernés, "
      "alors qu'ils auraient pu l'être ? « - » s'il n'y a rien à exclure."),
    Q("POURQUOI (Est)", "Pourquoi est-ce un problème ? Quelle exigence n'est pas respectée, "
      "quelles conséquences ? (Il ne s'agit pas encore des causes.)"),
    Q("POURQUOI (N'est pas)", "Quelles exigences restent respectées, quelles "
      "conséquences ne sont pas en jeu ? « - » si cela n'apporte rien."),
]

QQOQCCP_GENERAL = [
    Q("QUOI (Est)", "Quel est précisément le sujet : le défaut ou l'écart constaté (produit ou "
      "processus), ou plus largement la problématique à traiter ? Qu'est-ce qui est touché ?"),
    Q("QUOI (N'est pas)", "Qu'est-ce qui pourrait être concerné mais ne l'est pas (autres "
      "aspects, autres produits ou processus, ce qui fonctionne bien) ? « - » s'il n'y a rien à exclure."),
    Q("QUI (Est)", "Qui est concerné (postes, équipes, clients, fournisseurs) ? Qui a constaté la "
      "situation ?"),
    Q("QUI (N'est pas)", "Qui pourrait être concerné mais ne l'est pas (autres postes, "
      "équipes, clients) ? « - » s'il n'y a rien à exclure."),
    Q("OÙ (Est)", "À quel endroit la situation apparaît-elle (site, ligne, poste, service, étape du "
      "processus), et où est-elle constatée ?"),
    Q("OÙ (N'est pas)", "Où pourrait-elle apparaître mais n'apparaît pas (autres sites, "
      "services, postes, étapes) ? « - » s'il n'y a rien à exclure."),
    Q("QUAND (Est)", "Depuis quand, à quelle fréquence, à quel moment (équipe, période, changement, "
      "démarrage) ?"),
    Q("QUAND (N'est pas)", "Quand ne se produit-elle pas (périodes, équipes ou moments "
      "sans le problème) ? « - » s'il n'y a rien à exclure."),
    Q("COMMENT (Est)", "Comment la situation se manifeste-t-elle, dans quelles conditions, et "
      "comment a-t-elle été constatée (observation, mesure, réclamation, retour d'expérience) ?"),
    Q("COMMENT (N'est pas)", "Comment ne se manifeste-t-elle pas (autres formes ou "
      "conditions non observées) ? « - » s'il n'y a rien à exclure."),
    Q("COMBIEN (Est)", "Quelle ampleur : quantité, fréquence, taux, durée, coût ?"),
    Q("COMBIEN (N'est pas)", "Quelle part ou quelles quantités ne sont pas concernées, "
      "alors qu'elles auraient pu l'être ? « - » s'il n'y a rien à exclure."),
    Q("POURQUOI (Est)", "Pourquoi est-ce un sujet à traiter ? Quelle exigence ou quel objectif "
      "n'est pas atteint, quels enjeux ou conséquences ? (Il ne s'agit pas encore des causes.)"),
    Q("POURQUOI (N'est pas)", "Quelles exigences ou quels objectifs sont respectés, "
      "quelles conséquences ne sont pas en jeu ? « - » si cela n'apporte rien."),
]

QUESTIONS = {
    ("8d", "D1"): [
        Q("Identification", "Donnez un titre à la problèmatique, avec la référence du produit ou du "
          "processus concerné (et son indice de version), le numéro de lot ou de série, la date et le numéro "
          "de litige ou de réclamation s'il existe."),
        Q("Écart attendu / réel", "Quel était l'attendu (standard, plan, cahier des charges, norme, attente "
          "client) et que constate-t-on en réalité ? Quel est l'écart ?"),
        *QQOQCCP,
        Q("Risque", "Le problème est-il récurrent ? D'autres produits, lignes ou processus peuvent-ils être "
          "touchés ?"),
        Q("Enjeu", "Quel est l'enjeu : risque client, coût, logistique (livraison, délai) ?"),
    ],
    ("8d", "D2"): [
        Q("Pilote", "Qui est le pilote 8D (nom, fonction) ?"),
        Q("Client et sponsor", "Quel est le client concerné, et quel manager / sponsor est notifié de "
          "l'ouverture du 8D et de son rythme de suivi ?"),
        Q("Équipe", "Qui compose l'équipe (3 à 5 membres : nom, fonction — terrain, encadrement, experts, "
          "éventuellement client ou fournisseur) ?"),
        Q("Fonctions manquantes", "Quelles fonctions faut-il encore mobiliser (Études, Maintenance, Méthodes, "
          "Qualité, Fabrication, Achats, Logistique, Opérateurs) ?"),
        Q("Fonctionnement", "Comment l'équipe fonctionne-t-elle : objectifs, rôles et responsabilités, lieu et "
          "rythme des réunions, communication interne et externe ?"),
    ],
    ("8d", "D3"): [
        Q("Reconnaître le problème", "Comment reconnaît-on le problème (symptôme caractéristique) ? Faut-il le "
          "décomposer en plusieurs problèmes similaires apparus en des endroits différents ?"),
        Q("Flux", "Décrivez le flux (matières, pièces, informations) depuis son début jusqu'au point où le "
          "problème est détecté : quelles sont les étapes ?"),
        Q("Occurrence potentielle", "À quelles étapes du flux le problème a-t-il pu naître ?"),
        Q("Non-détection potentielle", "À quelles étapes aurait-il dû être arreté ? Quels contrôles existent "
          "et pourquoi n'ont-ils pas joué leur rôle ?"),
    ],
    ("8d", "D4"): [
        Q("Actions de sécurisation", "Quelles actions immédiates protègent le client (quarantaine, tri, "
          "retouche, rebut, contrôle renforcé…) ?"),
        Q("Qui, quand, coût", "Pour chaque action : qui, quand, quel coût, et qu'est-ce qui prouve son "
          "efficacité ?"),
        Q("Information et escalade", "Tous les services concernés sont-ils informés ? Le processus d'escalade "
          "(autres unités, Direction) est-il respecté ?"),
        Q("Contrôle des effets", "Comment vérifiez-vous que le client est protégé à 100 % ? Le client est-il "
          "d'accord (son autorisation est-elle nécessaire) ?"),
    ],
    ("8d", "D5"): [
        Q("Analyse des causes (6M, FTA, 5 Pourquoi)",
          "Analyse en trois temps : Ishikawa 6M (occurrence, puis non-détection), FTA de chaque cause, "
          "5 Pourquoi sur les causes prouvées."),
    ],
    ("8d", "D6"): [
        Q("Nature des causes", "Quelle est la nature de chaque cause racine : technique, systémique ou "
          "management ?"),
        Q("Actions", "Quelles actions correctives et préventives pour chaque cause racine (plusieurs solutions "
          "si possible) ?"),
        Q("Choix et risques", "Comment les avez-vous choisies (coût, délai, facilité, efficacité) ? Quels "
          "risques d'effet néfaste (AMDEC) ?"),
        Q("Plan d'actions", "Plan d'actions : qui fait quoi, pour quand ?"),
        Q("Standardisation", "Quels standards (procédures, instructions, modes opératoires) seront modifiés, "
          "et comment suivre leur respect (VRS) ?"),
        Q("Validation management", "Le management a-t-il validé le choix des actions ?"),
    ],
    ("8d", "D7"): [
        Q("Résultats mesurés", "Quels résultats mesurés montrent que le problème a disparu ? (indicateurs, "
          "valeurs avant / après)"),
        Q("Robustesse", "Sur quelle durée et avec quels indicateurs surveillez-vous la non-réapparition ? "
          "Observe-t-on des dérives ?"),
        Q("Transversalisation", "Quels autres produits, lignes, processus ou sites similaires sont concernés ?"),
        Q("Documents mis à jour", "Quels standards, AMDEC, plan de surveillance, management visuel et lessons "
          "learned sont mis à jour ?"),
    ],
    ("8d", "D8"): [
        Q("Reconnaissance de l'équipe", "Comment l'effort collectif est-il reconnu, et comment le 8D est-il clos "
          "(date, validation par l'autorité requise) ?"),
        Q("Information et archivage", "À qui l'information est-elle remontée (management), et où le rapport 8D "
          "final est-il diffusé et archivé ?"),
        Q("Lessons learned", "Quelles lessons learned versez-vous à la base de connaissances ?"),
    ],

    ("a3", "1"): [
        Q("Pourquoi ce sujet", "Pourquoi ce sujet, et pourquoi maintenant ?"),
        Q("Enjeux", "Quel est l'enjeu pour le client, la sécurité, la qualité, les coûts, les délais ?"),
        *QQOQCCP_GENERAL,
        Q("Indicateurs", "Quels indicateurs (KPI) mesurent ce sujet, et à quel objectif de l'entreprise "
          "sont-ils liés ?"),
    ],
    ("a3", "2"): [
        Q("Observation terrain", "Qu'avez-vous vu sur le lieu même du problème (gemba) ?"),
        Q("Écart", "Quel était le standard attendu et quel est le réel (chiffres) ?"),
        Q("Quantification", "Fréquence, localisation, tendance : que montrent vos relevés ?"),
        Q("Conforme / non conforme", "Qu'est-ce qui change entre le conforme et le non conforme ?"),
    ],
    ("a3", "3"): [
        Q("Valeur de départ", "Quelle est la valeur de départ (situation actuelle chiffrée) ?"),
        Q("Cible court terme", "Quelle valeur cible et quelle échéance à court terme (gain rapide) ?"),
        Q("Cible long terme", "Quelle est la cible à long terme (ambition durable) ?"),
        Q("Indicateurs", "Quels indicateurs de résultat et de processus suivrez-vous ?"),
    ],
    ("a3", "4"): [
        Q("Causes possibles", "Quelles causes possibles voyez-vous (6M : Machine, Matière, Main-d'œuvre, "
          "Méthodes, Milieu, Mesure) ?"),
        Q("Vérification terrain", "Lesquelles avez-vous vérifiées sur le terrain ? Avec quelles preuves ?"),
        Q("5 Pourquoi", "5 Pourquoi : jusqu'à quelle cause racine descendez-vous (une cause sur laquelle on "
          "peut agir) ?"),
        Q("Liens entre causes", "Y a-t-il des liens entre les causes (arbre des causes) ?"),
    ],
    ("a3", "5"): [
        Q("Contre-mesures", "Quelles contre-mesures pour chaque cause racine (plusieurs solutions) ?"),
        Q("Critères de choix", "Selon quels critères avez-vous choisi (coût, délai, efficacité, impact / "
          "effort) ?"),
        Q("Effet attendu", "Quel effet attendez-vous de chaque contre-mesure sur sa cause racine ?"),
        Q("Impacts", "Quel impact sur les autres équipes et sur le client ?"),
    ],
    ("a3", "6"): [
        Q("Qui fait quoi", "Qui fait quoi, pour quand ?"),
        Q("Jalons et suivi", "Quels jalons, et quel suivi (management visuel, fréquence) ?"),
        Q("Un changement à la fois", "Engagez-vous les actions une à la fois pour comprendre leurs impacts ?"),
    ],
    ("a3", "7"): [
        Q("Résultats", "Quels résultats mesurés, avant / après, par rapport à l'état initial et à l'objectif ?"),
        Q("Effets négatifs", "Observe-t-on un effet négatif ailleurs ?"),
        Q("Standardisation", "Que standardisez-vous (standard de travail, procédures) ? Sinon, quels "
          "ajustements ou quel nouveau cycle ?"),
    ],
    ("a3", "8"): [
        Q("Enseignements", "Qu'avez-vous appris sur le produit, le processus, votre façon de travailler ?"),
        Q("Partage", "Qui d'autre peut en profiter (sujets similaires) ?"),
        Q("Nouveau sujet", "Quel nouveau sujet ouvrez-vous ?"),
    ],

    ("pdca", "P"): [
        Q("Sujet", "Quel problème ou écart voulez-vous corriger ? Est-ce le bon sujet pour le client, pour "
          "l'opérateur ?"),
        Q("Situation et objectif", "Quelle est la situation actuelle (chiffrée) et l'objectif visé ?"),
        Q("Hypothèses de causes", "Quelles causes hypothétiques (Ishikawa, 5 Pourquoi) ?"),
        Q("Confirmation terrain", "Lesquelles avez-vous confirmées directement sur le terrain (gemba) ?"),
        Q("Contre-mesure", "Quelle contre-mesure envisagez-vous, avec quelles équipes ?"),
        Q("Impacts", "Quel impact anticipez-vous sur les autres équipes et sur le client ?"),
    ],
    ("pdca", "D"): [
        Q("Changement engagé", "Quel changement avez-vous réellement engagé (un seul à la fois) ?"),
        Q("Qui, quand, où", "Qui l'a mis en œuvre, quand et où ?"),
        Q("Écarts au plan", "Quels écarts par rapport au plan d'actions ?"),
    ],
    ("pdca", "C"): [
        Q("Résultats", "Quels résultats mesurés, avant / après, sur les mêmes indicateurs ?"),
        Q("Écarts", "Quels écarts avec l'objectif, et pourquoi ?"),
        Q("Effets négatifs", "Observe-t-on un effet négatif ailleurs ?"),
    ],
    ("pdca", "A"): [
        Q("À corriger", "Que faut-il corriger ? Qu'est-ce qui n'a pas marché, quelle erreur de raisonnement ?"),
        Q("À abandonner", "Que doit-on abandonner ?"),
        Q("Enseignements", "Qu'avez-vous appris sur le produit, le processus ou le geste ? Qui cela peut-il "
          "intéresser ?"),
        Q("Standardisation", "Que standardisez-vous, si les résultats sont satisfaisants ?"),
        Q("Nouveau cycle", "Quel nouveau cycle PDCA lancez-vous maintenant, et sur quoi ?"),
    ],

    ("k6pts", "K1"): [
        Q("Problème partagé", "Sommes-nous d'accord sur le problème à résoudre ? Formulez-le SANS solution."),
        *QQOQCCP_GENERAL,
    ],
    ("k6pts", "K2"): [
        Q("Faits relevés", "Quels faits avez-vous relevés (observation directe, mesures, temps) ?"),
        Q("Travail réel", "Décrivez le travail réel en détail : étapes, déplacements, gestes."),
        Q("Schéma ou photo", "Avez-vous un schéma, un diagramme spaghetti ou une photo du poste ? "
          "(vous pouvez envoyer la photo, puis décrire ce qu'elle montre)"),
        Q("Opportunité", "Y a-t-il bien une opportunité d'amélioration ? Laquelle ?"),
    ],
    ("k6pts", "K3"): [
        Q("Principes", "Quels PRINCIPES d'amélioration pourraient répondre au sujet ? "
          "(Un principe est une idée générale, pas encore un moyen concret.)"),
        Q("Solutions", "Pour chaque principe, quelles SOLUTIONS possibles (moyens concrets de l'appliquer) ? "
          "On ne choisit pas encore."),
    ],
    ("k6pts", "K4"): [
        Q("Ce qui sera testé", "Qu'allez-vous tester ?"),
        Q("Durée", "Pendant combien de temps ?"),
        Q("Mesures", "Avec quelles mesures ?"),
        Q("Critères et responsables", "Selon quels critères de réussite ? Qui teste, et où ?"),
    ],
    ("k6pts", "K5"): [
        Q("Test réalisé", "Qu'avez-vous réellement testé, et comment ?"),
        Q("Résultats", "Qu'est-ce qui a marché, qu'est-ce qui n'a pas marché (résultats mesurés) ?"),
        Q("Variables", "Quelles variables ont influencé le résultat ?"),
        Q("Idée efficace", "Quelle idée est réellement efficace ?"),
    ],
    ("k6pts", "K6"): [
        Q("Validation", "L'idée retenue est-elle validée par l'équipe ?"),
        Q("Intégration", "Comment l'intégrez-vous (standard, règles de sécurité, pratiques de production) ?"),
        Q("Déploiement", "Comment la déployez-vous plus largement (postes similaires) ?"),
        Q("Prochain sujet", "Quel sera le prochain sujet d'amélioration ?"),
    ],
}
for (_cle, _code), _qs in QUESTIONS.items():
    for _e in METHODES[_cle]["etapes"]:
        if _e["code"] == _code:
            _e["questions"] = _qs
for _m in METHODES.values():                       # étape sans questions écrites : les points à couvrir
    for _e in _m["etapes"]:
        _e.setdefault("questions", [Q(p.split(" :")[0][:40], p) for p in _e["points"]])

OK_WORDS = {"ok", "oui", "valider", "valide", "c'est bon", "suivant", "next"}
ACQUIT = OK_WORDS - {"oui"}           # « ok » & co. ne sont jamais une réponse à une question de l'étape
QUITTER = ("q", "quit", "quitter", "exit")

SYSTEM_PROMPT = (
    "Tu es un coach expert en résolution de problèmes (Lean, 8D, A3, PDCA, Kaizen 6 points). "
    "Tu réponds en français, avec le vouvoiement, de façon concise (150 mots maximum), "
    "claire et bienveillante mais exigeante. Tu t'appuies uniquement sur ce que "
    "l'utilisateur a dit ou sur ce qui est visible : tu n'inventes jamais de faits, de chiffres "
    "ni de causes. Tu te réfères aux « points à couvrir » de l'étape et tu signales ceux qui ne sont "
    "pas encore traités. Tu repères : les affirmations floues ou sans données, les solutions déguisées "
    "en causes, les causes qui accusent une personne au lieu d'un processus, les objectifs "
    "non mesurables. Tu poses des questions pour faire progresser l'utilisateur plutôt "
    "que de tout écrire à sa place. Tes questions et explications restent générales : jamais "
    "d'exemple concret ni propre à un secteur, pas de jargon non expliqué, pour être compréhensibles "
    "par tout public. Tu tiens compte des échanges précédents : tu ne répètes pas "
    "une remarque déjà faite."
)

# ----------------------------------------------------------------------------
# Aide : une seule description des commandes, présentée en tableau (terminal) ou en liste (Telegram)
# (commande, rôle, où) — où : "" partout · "tg" Telegram seulement · "cli" terminal seulement
# ----------------------------------------------------------------------------
AIDE_SECTIONS = [
    ("DÉMARCHES", [
        ("/8d [titre]", "8D : Résolution en équipe, sur 8 étapes & Synthèse", ""),
        ("/a3 [titre]", "A3 : du contexte jusqu'aux enseignements, sur une feuille A3", ""),
        ("/pdca [titre]", "PDCA (cycle court) : Planifier, Réaliser, Vérifier, Ajuster", ""),
        ("/k6pts [titre]", "Kaizen 6 points    : Observer, Tester, Retenir ce qui marche", ""),
    ]),
    ("PENDANT UNE DÉMARCHE", [
        ("(votre réponse)", "Répondez à la question ; le coach relance si nécessaire", ""),
        ("  -    ou   /sauter", "Laisse la question sans réponse", ""),
        ("/prec", "Revient à la question précédente", ""),
        ("phrase avec ?", "Pose une question au coach (la question en cours reste posée)", ""),
        ("photo", "Envoyez une photo (légende = question) : analysée, jointe au rapport", "tg"),
        ("/photo <chemin> [légende]", "Analyse une image, jointe au rapport", "cli"),
        ("/ok    ou    ok", "Valide l'étape", ""),
        ("/passer     /retour", "Passe l'étape / Revient à l'étape précédente", ""),
        ("/reponse    /effacer", "Relit vos réponses de l'étape / Recommence l'étape", ""),
        ("/guide      /etat", "Rappelle l'attendu et la question / Résumé des étapes", ""),
        ("/oui  /non  /fin", "D5 : réponse FTA / fin d'un facteur ou des pourquoi", ""),
    ]),
    ("MODE DU COACH", [
        ("/mode guide", "Questions une par une, relance de l'IA si besoin (par défaut)", ""),
        ("/mode rapide", "Questions une par une, sans IA (plus rapide en local)", ""),
        ("/mode libre", "Vous écrivez librement, le coach commente l'ensemble", ""),
    ]),
    ("PROJETS", [
        ("/projets", "Liste de vos projets", ""),
        ("/charger <n>", "Ré-ouvre un projet avec tout son historique", ""),
        ("/sauver     /stop", "Enregistre (automatiquement) / Met de côté", ""),
        ("/titre [texte]", "Définit ou modifie le titre, à tout moment", ""),
        ("/historique [étape|tout]", "Relit les échanges conservés", ""),
        ("/rapport [complet]", "Génère le rapport (complet avec tous les échanges)", ""),
        ("/supprimer [n]", "Efface un projet (avec confirmation)", ""),
    ]),
    ("MOTEUR IA", [
        ("/modele", "Choisit le moteur (Groq ou Ollama) et les modèles", ""),
        ("/backend groq|ollama", "Raccourci pour changer de moteur", ""),
    ]),
    ("AIDE ET SORTIE", [
        ("/aide  ou   /help", "Affiche ce menu", ""),
        ("/q     ou   /quit", "Quitte le programme (& enregistre le projet)", "cli"),
    ]),
]
# Menu court affiché au lancement du terminal : l'essentiel sur quelques lignes
AIDE_COURTE = [
    ("/8d  /a3  /pdca     /k6pts", "Démarre une démarche (ajouter un titre)"),
    ("/projets  /charger <n>", "Liste / Ré-ouvre un projet"),
    ("/ok       /sauter   /prec", "Valide l'étape / Avance / Retour arrière"),
    ("/modele", "Moteur et modèles"),
    ("/aide  ou  /help", "Menu complet"),
    ("/q     ou  /quit", "Quitte (& enregistre le projet)"),
]
AIDE_CONFIDENTIALITE = ("Confidentialité : avec Groq, les photos partent dans le cloud ; avec Ollama, elles "
                        "restent sur le Pi5.\n   Évitez visages et infos confidentiels.")

def aide_texte():
    """Menu en liste (Telegram, ou terminal étroit) : sections puis « commande — rôle »."""
    lignes = ["🧭 Problem Solving — Coach en Résolution de Problèmes"]
    for titre, rangs in AIDE_SECTIONS:
        lignes += ["", f"🔹 {titre}"]
        lignes += [f"{c} — {d}" for c, d, ou in rangs if ou != "cli"]
    lignes += ["", f"🔒 {AIDE_CONFIDENTIALITE}"]
    return "\n".join(lignes)

def aide_tableau(largeur=None, court=False):
    """Menu en tableau à deux colonnes (terminal), compact : le titre de chaque section est
    inscrit dans le trait de séparation (une ligne par section). court=True : l'essentiel
    seulement (écran de lancement)."""
    import textwrap
    largeur = min(largeur or largeur_terminal(), 100)
    blocs = ([("COMMANDES ESSENTIELLES", [(c, d, "") for c, d in AIDE_COURTE])]
             if court else AIDE_SECTIONS)
    rangs = [(c, d) for _, lst in blocs for c, d, ou in lst if ou != "tg"]
    c1 = min(max(len(c) for c, _ in rangs), 30)
    c2 = max(30, largeur - c1 - 7)
    def trait(g, m, d, titre=""):
        gauche = ("─ " + titre + " ").ljust(c1 + 2, "─") if titre else "─" * (c1 + 2)
        return g + gauche + m + "─" * (c2 + 2) + d
    sortie = []
    for k, (titre, lst) in enumerate(blocs):
        sortie.append(trait("┌" if k == 0 else "├", "┬" if k == 0 else "┼",
                            "┐" if k == 0 else "┤", titre))
        for c, d, ou in lst:
            if ou == "tg":
                continue
            cellule1 = textwrap.wrap(c, c1) or [""]
            cellule2 = textwrap.wrap(d, c2) or [""]
            for i in range(max(len(cellule1), len(cellule2))):
                x = cellule1[i] if i < len(cellule1) else ""
                y = cellule2[i] if i < len(cellule2) else ""
                sortie.append(f"│ {x.ljust(c1)} │ {y.ljust(c2)} │")
    sortie.append(trait("└", "┴", "┘"))
    if not court:
        sortie.append("🔒 " + AIDE_CONFIDENTIALITE)
    return "\n".join(sortie)

AIDE = aide_texte()

# ----------------------------------------------------------------------------
# Messages (texte + boutons facultatifs) — un bouton = une commande
# ----------------------------------------------------------------------------
def msg(texte, boutons=None, remplace=False):
    """Message sortant. str simple, ou dict {texte, boutons, remplace} si boutons.
    boutons : liste de lignes de (libellé, commande). remplace : à afficher à la place du
    message d'origine (menus), quand l'action vient d'un bouton."""
    if not boutons and not remplace:
        return texte
    return {"texte": texte, "boutons": boutons or [], "remplace": remplace}

def texte_de(m):
    return m["texte"] if isinstance(m, dict) else m

def boutons_de(m):
    return m.get("boutons", []) if isinstance(m, dict) else []

BTN_DEMARRER = [[("🔧 8D", "/8d"), ("📄 A3", "/a3")],
                [("🔄 PDCA", "/pdca"), ("🌱 Kaizen 6 pts", "/k6pts")],
                [("📂 Projets", "/projets"), ("🤖 Modèle", "/modele")]]
BTN_ETAPE = [[("📊 État", "/etat"), ("📜 Historique", "/historique")],
             [("↩️ Retour", "/retour"), ("⏭️ Passer", "/passer")],
             [("💾 Sauver", "/sauver"), ("✏️ Titre", "/titre"), ("🤖 Modèle", "/modele")]]
BTN_RETOUR_COACH = [[("✅ Valider l'étape", "/ok"), ("🧾 Ma réponse", "/reponse")]]
BTN_QUESTION = [[("⏭️ Sauter", "/sauter"), ("↩️ Précédente", "/prec")],
                [("🧾 Mes réponses", "/reponse"), ("📊 État", "/etat")]]
BTN_FIN_QUESTIONS = [[("✅ Valider l'étape", "/ok"), ("🧾 Mes réponses", "/reponse")],
                     [("↩️ Question précédente", "/prec")]]
BTN_RCA_ISH = [[("➡️ Facteur suivant", "/sauter"), ("↩️ Annuler la dernière", "/prec")]]
BTN_RCA_FTA = [[("✅ Oui", "/oui"), ("❌ Non", "/non")],
               [("⏭️ Cause non évaluée", "/sauter"), ("↩️ Annuler", "/prec")]]
BTN_RCA_PQ = [[("🏁 Cause racine atteinte", "/fin"), ("↩️ Annuler", "/prec")]]
BTN_FIN = [[("📄 Rapport complet", "/rapport complet"), ("📂 Projets", "/projets")],
           [("↩️ Ré-ouvrir la dernière étape", "/retour")]]

# ----------------------------------------------------------------------------
# Fichiers
# ----------------------------------------------------------------------------
def ecrire_json(chemin, data):
    """Écriture atomique (pas de fichier tronqué si le Pi s'arrête en cours d'écriture)."""
    tmp = chemin.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, chemin)

def lire_json(chemin, defaut=None):
    try:
        return json.loads(Path(chemin).read_text(encoding="utf-8"))
    except Exception:
        return defaut

def coupe(texte, n):
    texte = (texte or "").strip()
    return texte if len(texte) <= n else texte[:n].rstrip() + "…"

# ----------------------------------------------------------------------------
# Appels LLM
# ----------------------------------------------------------------------------
def _delai_retry(r):
    """Délai conseillé par Groq après un 429 (secondes)."""
    try:
        return float(r.headers.get("retry-after", ""))
    except (TypeError, ValueError):
        pass
    m = re.search(r"try again in ([\d.]+)s", r.text or "")
    return float(m.group(1)) if m else 30.0

def _groq_chat(messages, model, max_tokens=1200):
    if not CFG["GROQ_API_KEY"]:
        raise RuntimeError("clé Groq introuvable (GROQ_API_KEY ou ~/Projects/Groq_agent/.groq_config)")
    payload = {"model": model, "messages": messages, "temperature": 0.3, "max_tokens": max_tokens}
    if "gpt-oss" in model:
        payload["reasoning_effort"] = "low"
    r = None
    for essai in range(2):
        r = requests.post(GROQ_URL, headers={"Authorization": f"Bearer {CFG['GROQ_API_KEY']}"},
                          json=payload, timeout=90)
        if r.status_code == 429 and essai == 0 and _delai_retry(r) <= 20:
            time.sleep(_delai_retry(r) + 0.5)
            continue
        break
    r.raise_for_status()
    texte = (r.json()["choices"][0]["message"].get("content") or "").strip()
    texte = re.sub(r"<think>.*?</think>", "", texte, flags=re.S).strip()
    if not texte:
        raise RuntimeError("réponse vide du modèle (raisonnement trop long ?)")
    return texte

def _ollama_chat(messages, model, images=None, num_predict=500):
    msgs = [dict(m) for m in messages]
    if images:
        msgs[-1]["images"] = images
    r = requests.post(
        f"{CFG['OLLAMA_URL']}/api/chat",
        json={"model": model, "messages": msgs, "stream": False,
              "options": {"temperature": 0.3, "num_predict": num_predict}},
        timeout=600,
    )
    r.raise_for_status()
    texte = (r.json()["message"]["content"] or "").strip()
    texte = re.sub(r"<think>.*?</think>", "", texte, flags=re.S).strip()
    if not texte:
        raise RuntimeError("réponse vide du modèle")
    return texte

def _code_http(e):
    return e.response.status_code if isinstance(e, requests.HTTPError) and e.response is not None else 0

def llm_texte(messages, backend, modele, max_tokens=None):
    """Appel texte. Retourne (texte, modèle_réellement_utilisé).
    Groq : repli automatique sur un autre modèle (quota séparé) en cas de 429/413.
    max_tokens : plafond de sortie (réponses courtes : relances, titres)."""
    if backend == "groq":
        kw = {"max_tokens": max_tokens} if max_tokens else {}
        try:
            return _groq_chat(messages, modele, **kw), modele
        except requests.HTTPError as e:
            repli = CFG["GROQ_FALLBACK_MODEL"]
            if repli == modele:                       # déjà sur le modèle de repli : on remonte d'un cran
                repli = CFG["GROQ_MODEL"]
            if _code_http(e) in (413, 429) and repli and repli != modele:
                return _groq_chat(messages, repli, **kw), repli
            raise
    kw = {"num_predict": max_tokens} if max_tokens else {}
    return _ollama_chat(messages, modele, **kw), modele

def preparer_image(donnees):
    """Retourne une image JPEG prête à l'envoi (rotation EXIF, max 1280 px)."""
    if len(donnees) > IMAGE_MAX_OCTETS:
        raise ValueError("image trop volumineuse (20 Mo max)")
    if Image is None:
        if len(donnees) > IMAGE_MAX_ENVOI:
            raise ValueError("image trop lourde : installez pillow (pip install pillow "
                             "--break-system-packages) pour la redimensionner")
        return donnees
    try:
        img = Image.open(io.BytesIO(donnees))
        img.load()
    except Exception:
        raise ValueError("format d'image non reconnu (JPEG, PNG, WebP attendus)")
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    img = img.convert("RGB")
    img.thumbnail((1280, 1280))
    tampon = io.BytesIO()
    img.save(tampon, format="JPEG", quality=85)
    return tampon.getvalue()

def llm_vision(prompt, image_jpeg, backend, modele_vision, modele_texte):
    """Analyse d'une image (JPEG) avec le modèle vision du moteur choisi."""
    b64 = base64.b64encode(image_jpeg).decode()
    if backend == "groq":
        msgs = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
            ]},
        ]
        return _groq_chat(msgs, modele_vision, max_tokens=1500)

    if "moondream" in modele_vision.lower():
        # Moondream comprend l'anglais seulement : il décrit, le modèle texte répond en français.
        desc = _ollama_chat(
            [{"role": "user", "content": "Describe this image in detail. Read any visible text, "
                                         "numbers and labels. Do not guess what is not visible."}],
            modele_vision, images=[b64], num_predict=300)
        msgs = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt + "\n\nDescription factuelle de la photo (générée "
             "par un modèle de vision anglophone, peut contenir des erreurs) :\n" + desc},
        ]
        return _ollama_chat(msgs, modele_texte, num_predict=500)
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
    return _ollama_chat(msgs, modele_vision, images=[b64], num_predict=600)

def msg_erreur(e, backend):
    autre = "ollama" if backend == "groq" else "groq"
    code = _code_http(e)
    if code == 429:
        detail = "quota atteint, réessayez dans une minute"
    elif code == 413:
        detail = "requête trop volumineuse"
    elif code in (401, 403):
        detail = "clé API refusée"
    elif code == 404:
        detail = ("modèle introuvable côté Ollama (vérifiez `ollama list` / `ollama pull`)"
                  if backend == "ollama" else "modèle introuvable chez Groq")
    elif code == 400:
        detail = "requête refusée par le modèle (accepte-t-il les images ?)"
    elif code:
        detail = f"erreur HTTP {code}"
    elif isinstance(e, requests.Timeout):
        detail = "délai dépassé"
    elif isinstance(e, requests.ConnectionError):
        detail = "service injoignable" + (" (Ollama démarré ? sudo systemctl start ollama)"
                                           if backend == "ollama" else "")
    else:
        detail = f"{type(e).__name__}: {e}"
    return f"⚠️ Moteur « {backend} » : {detail}. Réessayez, ou /modele (ou /backend {autre})."

# ----------------------------------------------------------------------------
# Catalogue des modèles
# ----------------------------------------------------------------------------
_cache_ollama = {"t": 0.0, "noms": []}

def ollama_installes():
    """Noms des modèles installés sur Ollama (cache 30 s ; [] si le service est arrêté)."""
    if time.time() - _cache_ollama["t"] < 30:
        return list(_cache_ollama["noms"])
    try:
        r = requests.get(f"{CFG['OLLAMA_URL']}/api/tags", timeout=3)
        r.raise_for_status()
        noms = sorted(m["name"] for m in r.json().get("models", []) if m.get("name"))
    except Exception:
        noms = []
    _cache_ollama.update(t=time.time(), noms=noms)
    return list(noms)

def _avec(liste, defaut):
    """Ajoute le modèle par défaut (variable d'environnement) s'il n'est pas dans la liste."""
    if defaut and defaut not in [m[0] for m in liste]:
        liste = liste + [(defaut, defaut, "défaut .env")]
    return liste

def catalogue(backend, usage):
    """Liste [(id, libellé, description)] des modèles choisissables (usage : texte | vision)."""
    if backend == "groq":
        base = GROQ_MODELES if usage == "texte" else GROQ_VISION_MODELES
        defaut = CFG["GROQ_MODEL"] if usage == "texte" else CFG["GROQ_VISION_MODEL"]
        return _avec(list(base), defaut)
    noms = ollama_installes()
    if usage == "vision":
        noms = [n for n in noms if INDICE_VISION.search(n)]
        defaut = CFG["OLLAMA_VISION_MODEL"]
    else:
        noms = [n for n in noms if "embed" not in n.lower()]
        defaut = CFG["OLLAMA_MODEL"]
    return _avec([(n, n, "installé") for n in noms], defaut)

def libelle_modele(backend, usage, modele):
    for mid, lib, _ in catalogue(backend, usage):
        if mid == modele:
            return lib
    return modele

# ----------------------------------------------------------------------------
# Session : utilisateur + projet actif
# ----------------------------------------------------------------------------
CHAMPS_PROJET = ("methode", "titre", "titre_propose", "idx", "reponses", "photos", "fil", "qr",
                 "brouillon", "fini", "debut", "maj", "rca")
MODES = {"guide": "guidé : une question à la fois, avec relance IA si la réponse est floue",
         "rapide": "rapide : une question à la fois, sans appel IA (idéal avec un modèle local lent)",
         "libre": "libre : vous écrivez la réponse de l'étape, le coach commente"}
RE_REPONSE_NEUTRE = re.compile(r"(non|oui|nsp|je ne sais pas|aucun|aucune|n/?a|rien|ok|d'accord)\.?", re.I)
LIB_FIL = {"u": "🧑 Vous", "c": "🧭 Coach", "q": "🧑 Vous (question)", "a": "🧭 Coach",
           "p": "📷 Photo", "s": "⚙️"}

# ----------------------------------------------------------------------------
# D5 : Ishikawa 6M (occurrence puis non-détection) → FTA → 5 Pourquoi
# L'état se déduit d'un journal d'événements (self.rca["ev"]) : annuler = retirer le dernier.
#   ["c", texte] cause · ["m"] fin du facteur · ["f", "oui"|"non", note] réponse FTA ·
#   ["fs"] cause non évaluée · ["p", texte] un pourquoi · ["pe"] cause racine atteinte
# ----------------------------------------------------------------------------
SIX_M = [("Machine", "équipements, outils, logiciels, moyens de production"),
         ("Matière", "matières, composants, produits ou informations reçus"),
         ("Main-d'œuvre", "compétences, formation, organisation du travail, charge (le processus, "
                          "jamais la personne)"),
         ("Méthodes", "procédures, instructions, gammes, standards, séquences de travail"),
         ("Milieu", "environnement : lieu, ambiance, ergonomie, rangement"),
         ("Mesure", "contrôles, mesures, moyens et critères de détection")]
DIMENSIONS = [("O", "OCCURRENCE", "Pourquoi le problème a-t-il pu apparaître"),
              ("N", "NON-DÉTECTION", "Pourquoi le problème n'a-t-il pas été détecté ni arrêté")]
FTA_QUESTIONS = [
    ("Standard existant", "Existe-t-il un standard (procédure, instruction, règle, plan de contrôle…) "
                          "qui encadre ce point ?"),
    ("Standard appliqué", "Ce standard est-il appliqué sur le terrain, par tous et en permanence ?"),
    ("Standard robuste", "Ce standard est-il robuste : clair, à l'épreuve des erreurs et des variations, "
                         "sans dépendre d'une personne ?")]
POURQUOI_MIN = 5
FIN_SAISIE = {"-", "ok", "fin", "non", "rien", "aucune", "aucun", "fini", "terminé", "termine", "suivant"}
RE_OUINON = re.compile(r"^\s*(oui|o|yes|y|non|n|no)\b[\s,.:;!\-]*(.*)$", re.I | re.S)

def rca_calcul(ev):
    """Rejoue le journal : phase (ish, fta, pq, fin), position courante, causes, verdicts, pourquoi."""
    S = {"ph": "ish", "dim": 0, "m": 0, "causes": [], "fta": {}, "fi": 0, "fq": 0,
         "prouvees": [], "pi": 0, "why": {}, "racine": {}}

    def suite():
        if S["ph"] == "ish" and S["dim"] >= 2:
            S["ph"] = "fta"
        if S["ph"] == "fta" and S["fi"] >= len(S["causes"]):
            S["prouvees"] = [c["id"] for c in S["causes"] if S["fta"].get(c["id"], {}).get("v") == "prouvee"]
            S["ph"] = "pq"
        if S["ph"] == "pq" and S["pi"] >= len(S["prouvees"]):
            S["ph"] = "fin"
    suite()
    for e in ev:
        k = e[0]
        if S["ph"] == "ish":
            if k == "c":
                n = 1 + sum(1 for c in S["causes"] if c["dim"] == S["dim"] and c["m"] == S["m"])
                S["causes"].append({"id": f"{DIMENSIONS[S['dim']][0]}{S['m'] + 1}.{n}",
                                    "dim": S["dim"], "m": S["m"], "t": e[1]})
            elif k == "m":
                S["m"] += 1
                if S["m"] >= len(SIX_M):
                    S["m"], S["dim"] = 0, S["dim"] + 1
        elif S["ph"] == "fta":
            c = S["causes"][S["fi"]]
            f = S["fta"].setdefault(c["id"], {"r": [], "v": None})
            if k == "f":
                f["r"].append([e[1], e[2] if len(e) > 2 else ""])
                if e[1] == "non":
                    f["v"], S["fi"], S["fq"] = "prouvee", S["fi"] + 1, 0
                else:
                    S["fq"] += 1
                    if S["fq"] >= len(FTA_QUESTIONS):
                        f["v"], S["fi"], S["fq"] = "ecartee", S["fi"] + 1, 0
            elif k == "fs":
                f["v"], S["fi"], S["fq"] = "non evaluee", S["fi"] + 1, 0
        elif S["ph"] == "pq":
            cid = S["prouvees"][S["pi"]]
            if k == "p":
                S["why"].setdefault(cid, []).append(e[1])
            elif k == "pe":
                S["racine"][cid] = True
                S["pi"] += 1
        suite()
    return S

def rca_lib(c):
    return f"{c['id']} ({SIX_M[c['m']][0]})"

def rca_diagramme(S, dim):
    lignes = [f"🐟 Ishikawa {DIMENSIONS[dim][1]}"]
    for m, (nom, _) in enumerate(SIX_M):
        cs = [c for c in S["causes"] if c["dim"] == dim and c["m"] == m]
        lignes.append(f"{m + 1} {nom}")
        lignes += [f"    {c['id']}  {c['t']}" for c in cs] or ["    (aucune cause)"]
    return "\n".join(lignes)

def rca_resume(S):
    """Texte de la réponse D5 : causes racines d'abord (le contexte transmis à D6 est tronqué par la fin)."""
    L = []
    prouvees = [c for c in S["causes"] if S["fta"].get(c["id"], {}).get("v") == "prouvee"]
    if S["ph"] != "fin":
        L.append("(analyse en cours)")
    if any(S["why"].values()):
        L.append("CAUSES RACINES (5 Pourquoi)")
        for c in prouvees:
            w = S["why"].get(c["id"])
            if w:
                L.append(f"- {rca_lib(c)} : « {c['t']} » → "
                         f"{'cause racine' if S['racine'].get(c['id']) else 'en cours'} : {w[-1]}")
        L += ["", "CHAÎNES DE POURQUOI"]
        for c in prouvees:
            w = S["why"].get(c["id"])
            if w:
                L.append(f"- {c['id']} : " + " → ".join(f"({i}) {x}" for i, x in enumerate(w, 1)))
    if prouvees:
        L += ["", "CAUSES PROUVÉES (FTA : au moins un « non »)"]
        for c in prouvees:
            r = S["fta"][c["id"]]["r"]
            i = next(i for i, x in enumerate(r) if x[0] == "non")
            note = f" — {r[i][1]}" if r[i][1] else ""
            L.append(f"- {rca_lib(c)} : « {c['t']} » — non : {FTA_QUESTIONS[i][0].lower()}{note}")
    ecartees = [c["id"] for c in S["causes"] if S["fta"].get(c["id"], {}).get("v") == "ecartee"]
    non_eval = [c["id"] for c in S["causes"] if S["fta"].get(c["id"], {}).get("v") == "non evaluee"]
    if ecartees:
        L += ["", "CAUSES ÉCARTÉES (standard existant, appliqué et robuste) : " + ", ".join(ecartees)]
    if non_eval:
        L += ["", "CAUSES NON ÉVALUÉES : " + ", ".join(non_eval)]
    for dim in (0, 1):
        cs = [c for c in S["causes"] if c["dim"] == dim]
        if cs or S["dim"] > dim or S["ph"] != "ish":
            L += ["", f"ISHIKAWA {DIMENSIONS[dim][1]}"]
            for m, (nom, _) in enumerate(SIX_M):
                x = [c for c in cs if c["m"] == m]
                L.append(f"- {m + 1} {nom} : " + (" ; ".join(f"{c['id']} {c['t']}" for c in x) if x else "—"))
    return "\n".join(L).strip()

class Session:
    def __init__(self, uid):
        self.uid = str(uid)
        self.dir = DATA_DIR / re.sub(r"[^\w.-]", "_", self.uid)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.prefs = self._prefs_defaut()
        self.attend_titre = False
        self.dernier_modele = ""
        self.erreur_llm = None
        self._vider_projet()
        self._migrer_ancien()
        self.prefs.update({k: v for k, v in (lire_json(self.dir / "_prefs.json", {}) or {}).items()
                           if k in ("backend_choisi", "modeles", "actif", "mode")})
        if self.prefs.get("actif"):
            if not self.ouvrir(self.prefs["actif"]):
                self.prefs["actif"] = None
        self.purger_vides()

    # -- projets vides : rien à garder -------------------------------------------
    @staticmethod
    def projet_vide(d):
        """Projet sans aucune saisie : ni réponse, ni échange, ni photo, ni cause D5."""
        if not isinstance(d, dict):
            return False
        return not (d.get("titre") not in (None, "", "Sans titre") or d.get("reponses") or d.get("fil") or d.get("photos") or (d.get("brouillon") or "").strip()
                    or (d.get("rca") or {}).get("ev")
                    or any((st or {}).get("r") or (st or {}).get("c") for st in (d.get("qr") or {}).values()))

    def purger_vides(self):
        """Supprime les projets vides (un /8d lancé puis abandonné), sauf le projet ouvert."""
        for f in self.dir.glob("*.json"):
            if f.name.startswith("_") or f.stem == self.pid:
                continue
            if self.projet_vide(lire_json(f)):
                try:
                    f.unlink()
                except OSError:
                    pass

    # -- préférences (moteur, modèles) -----------------------------------------
    @staticmethod
    def _prefs_defaut():
        # backend_choisi : renseigné UNIQUEMENT par /modele ; sinon BACKEND du .env (défaut : groq)
        return {"backend_choisi": None, "actif": None, "mode": "guide",
                "modeles": {"groq": {}, "ollama": {}}}

    @staticmethod
    def _modeles_defaut():
        return {"groq": {"texte": CFG["GROQ_MODEL"], "vision": CFG["GROQ_VISION_MODEL"]},
                "ollama": {"texte": CFG["OLLAMA_MODEL"], "vision": CFG["OLLAMA_VISION_MODEL"]}}

    def sauver_prefs(self):
        ecrire_json(self.dir / "_prefs.json", self.prefs)

    @property
    def backend(self):
        choisi = self.prefs.get("backend_choisi")
        if choisi in ("groq", "ollama"):
            return choisi
        return CFG["BACKEND"] if CFG["BACKEND"] in ("groq", "ollama") else "groq"

    @property
    def mode(self):
        return self.prefs.get("mode") if self.prefs.get("mode") in MODES else "guide"

    def modele(self, usage="texte"):
        m = (self.prefs.get("modeles") or {}).get(self.backend, {}).get(usage)
        return m or self._modeles_defaut()[self.backend][usage]

    # -- projet : état ---------------------------------------------------------
    def _vider_projet(self):
        self.pid = None
        self.methode = None
        self.titre = "Sans titre"
        self.titre_propose = ""
        self.idx = 0
        self.reponses = {}
        self.photos = {}          # code d'étape -> [{"legende", "analyse", "date"}]
        self.fil = {}             # code d'étape -> [{"r", "t", "d"}] : TOUS les échanges
        self.qr = {}              # mode guidé : code d'étape -> {"i": question en cours, "r": {n: réponse}, "rel": n|None}
        self.brouillon = ""       # réponse en cours (cumul de vos messages sur l'étape)
        self.rca = {"ev": []}     # D5 : journal des saisies (causes Ishikawa, réponses FTA, pourquoi)
        self.fini = False
        self.debut = ""
        self.maj = ""

    def _donnees_projet(self):
        return {k: getattr(self, k) for k in CHAMPS_PROJET}

    def save(self):
        """Enregistre le projet actif (appelé après chaque changement) et les préférences."""
        if self.pid:
            self.maj = datetime.now().strftime("%d/%m/%Y %H:%M")
            ecrire_json(self.dir / f"{self.pid}.json", self._donnees_projet())
        self.prefs["actif"] = self.pid
        self.sauver_prefs()

    def ouvrir(self, pid):
        """Charge un projet depuis le disque. False s'il est absent ou illisible."""
        d = lire_json(self.dir / f"{pid}.json")
        if not isinstance(d, dict) or d.get("methode") not in METHODES:
            return False
        self._vider_projet()
        for k in CHAMPS_PROJET:
            if k in d:
                setattr(self, k, d[k])
        self.pid = pid
        self.idx = min(max(int(self.idx or 0), 0), len(self.etapes) - 1)
        if self.brouillon and self.code not in self.qr and not self.fini:
            self.qetat()["c"] = self.brouillon           # projet saisi en mode libre : texte conservé
        st = self.qr.get("D5")
        if self.methode == "8d" and st and not (self.rca or {}).get("ev") \
                and any(k in (st.get("r") or {}) for k in ("1", "2", "3", "4")):
            anciens = ["6M — Machine, Matière, Main-d'œuvre", "6M — Méthodes, Milieu, Mesure",
                       "Facteurs prouvés (FTA)", "Cause racine d'occurrence", "Cause racine de non-détection"]
            st["r"] = {"0": "(ancien format de l'étape)\n" + "\n".join(
                f"{lib} : {st['r'][str(n)]}" for n, lib in enumerate(anciens) if st["r"].get(str(n)))}
            st["i"] = 1
        if not isinstance(self.rca, dict) or "ev" not in self.rca:
            self.rca = {"ev": []}
        return True

    def _migrer_ancien(self):
        """Ancien format : sessions/<id>.json (un seul projet, sans historique)."""
        ancien = DATA_DIR / f"{self.uid}.json"
        if not ancien.is_file():
            return
        d = lire_json(ancien, {}) or {}
        cle = d.get("methode")
        if cle in ("a3", "k6pts") and d.get("reponses"):
            self.pid = f"{datetime.now():%Y%m%d-%H%M%S}_{cle}"
            for k in CHAMPS_PROJET:
                if k in d:
                    setattr(self, k, d[k])
            self.fil = {}
            self.save()                       # reste le projet actif
        else:
            if cle in ("8d", "pdca") and d.get("reponses"):
                # étapes redéfinies d'après les synthèses : on archive l'ancien travail en Markdown
                lignes = [f"# {METHODES[cle]['nom']} (ancien format) — {d.get('titre', 'Sans titre')}", ""]
                for code, rep in d["reponses"].items():
                    lignes += [f"## {code}", "", rep or "_Non renseigné_", ""]
                (REPORT_DIR / f"ancien_{cle}_{self.uid}_{datetime.now():%Y%m%d_%H%M%S}.md").write_text(
                    "\n".join(lignes), encoding="utf-8")
            self._vider_projet()
            self.prefs["actif"] = None
            self.sauver_prefs()
        try:
            ancien.rename(ancien.with_suffix(".json.migre"))
        except OSError:
            pass

    # -- étapes ----------------------------------------------------------------
    @property
    def etapes(self):
        return METHODES[self.methode]["etapes"]

    @property
    def etape(self):
        return self.etapes[self.idx]

    @property
    def code(self):
        return self.etape["code"]

    # -- mode guidé : questions une par une --------------------------------------
    def guide_actif(self):
        return self.mode != "libre"

    def questions(self):
        return self.etape["questions"]

    def qetat(self, code=None):
        return self.qr.setdefault(code or self.code, {"i": 0, "r": {}, "rel": None})

    def questions_restantes(self):
        return max(0, len(self.questions()) - self.qetat()["i"])

    def assembler(self, code=None, sauf=None):
        """Réponses de l'étape, question par question (texte du rapport). sauf : n° à exclure."""
        code = code or self.code
        etape = next(e for e in self.etapes if e["code"] == code)
        st = self.qr.get(code) or {}
        lignes = []
        for n, (lib, _, _) in enumerate(etape["questions"]):
            rep = ((st.get("r") or {}).get(str(n)) or "").strip()
            if rep and n != sauf:
                lignes.append(f"- {lib} : " + rep.replace("\n", "\n  "))
        if st.get("c") and sauf is None:
            lignes.append("- Complément : " + st["c"].strip().replace("\n", "\n  "))
        return "\n".join(lignes)

    def synchroniser(self):
        """En mode guidé, la réponse de l'étape = l'assemblage des réponses aux questions."""
        if self.methode and self.guide_actif() and not self.fini:
            self.brouillon = self.assembler()

    def invite_texte(self):
        e = self.etape
        lignes = [f"📍 Étape {self.idx + 1}/{len(self.etapes)} — {e['code']} · {e['titre']}",
                  f"❓ {e['question']}",
                  f"🎯 Attendu : {e['attendu']}"]
        if self.rca_step():
            lignes.append(f"🛠 Outils : {e['outils']}")
            lignes.append("👉 Trois temps : (1) Ishikawa 6M, une cause à la fois, d'abord pour l'occurrence puis "
                          "pour la non-détection ; (2) FTA : pour chaque cause, un standard existe-t-il, "
                          "est-il appliqué, est-il robuste ? ; (3) 5 Pourquoi sur les causes prouvées "
                          "(au moins un « non » au FTA). /prec annule la dernière saisie.")
        elif self.guide_actif():
            n = len(self.questions())
            lignes.append(f"🛠 Outils : {e['outils']}")
            lignes.append(f"👉 Je vais vous poser {n} question{'s' if n > 1 else ''}, une par une. "
                          "« - » (ou /sauter) pour passer une question, /prec pour revenir.")
        else:
            lignes.append("À couvrir :")
            lignes += [f"• {p}" for p in e["points"]]
            lignes.append(f"🛠 Outils : {e['outils']}")
        if self.methode == "k6pts" and e["code"] == "K2":
            lignes.append("📷 Une photo du poste peut aider (envoyez-la, légende = votre question).")
        return "\n".join(lignes)

    def question_msg(self):
        if self.rca_actif():
            return self.rca_prompt()
        st = self.qetat()
        qs = self.questions()
        i = st["i"]
        if i >= len(qs):
            return msg("✅ Toutes les questions de l'étape sont traitées. Vous pouvez encore ajouter un "
                       "complément (écrivez-le), puis validez.", BTN_FIN_QUESTIONS)
        lib, q, ex = qs[i]
        texte = f"❓ Question {i + 1}/{len(qs)} — {lib}\n{q}"
        if ex:
            texte += f"\n💡 Exemple : {ex}"
        actuel = (st["r"].get(str(i)) or "").strip()
        if actuel:
            texte += f"\n✍️ Votre réponse actuelle : {coupe(actuel, 300)}\n(envoyez-en une nouvelle pour la remplacer)"
        return msg(texte, BTN_QUESTION)

    def invite_msgs(self):
        """Présentation de l'étape, puis (mode guidé) la question en cours."""
        if self.guide_actif():
            return [msg(self.invite_texte(), BTN_ETAPE), self.question_msg()]
        return [msg(self.invite_texte(), BTN_ETAPE)]

    def invite_etape(self):
        return self.invite_msgs()[0]

    def entete(self):
        m = METHODES[self.methode]
        e = self.etape
        points = "\n".join(f"  - {p}" for p in e["points"])
        return (f"Méthode : {m['nom']}. {m['note']}\nProblème : {self.titre}.\n"
                f"Étape : {e['code']} {e['titre']} — {e['question']}\nAttendu : {e['attendu']}\n"
                f"Points à couvrir :\n{points}\nOutils : {e['outils']}\n"
                f"Déjà validé :\n{self.contexte()}\n")

    def contexte(self):
        faits = [(e["code"], e["titre"], self.reponses[e["code"]]) for e in self.etapes
                 if self.reponses.get(e["code"])]
        if not faits:
            return "(rien de validé pour l'instant)"
        cap = max(250, min(700, CTX_MAX_CHARS // len(faits)))
        return "\n".join(f"- {c} {t} : {coupe(r, cap)}" for c, t, r in faits)

    # -- fil de la conversation ------------------------------------------------
    def noter(self, role, texte, code=None):
        self.fil.setdefault(code or self.code, []).append(
            {"r": role, "t": texte, "d": datetime.now().strftime("%d/%m/%Y %H:%M")})

    def fil_pour_llm(self):
        """Retours du coach, questions et réponses déjà échangés sur l'étape (les plus récents,
        dans la limite du budget). Vos propres messages sont déjà dans la réponse cumulée."""
        etiquettes = {"c": "Retour précédent du coach", "q": "Question de l'utilisateur",
                      "a": "Réponse du coach à la question"}
        sortie, total = [], 0
        for e in reversed(self.fil.get(self.code, [])):
            if e["r"] not in etiquettes:
                continue
            ligne = f"- {etiquettes[e['r']]} : {coupe(e['t'], FIL_ENTREE_MAX)}"
            if total + len(ligne) > FIL_MAX_CHARS:
                break
            sortie.append(ligne)
            total += len(ligne)
        if not sortie:
            return ""
        return "\nÉchanges précédents sur cette étape (du plus ancien au plus récent) :\n" + \
               "\n".join(reversed(sortie)) + "\n"

    def photos_etape(self):
        morceaux = [f"- {coupe(p['analyse'], 350)}" for p in self.photos.get(self.code, [])[-2:]]
        if not morceaux:
            return ""
        return ("\nPhotos jointes à cette étape (analyse IA, à vérifier sur le terrain) :\n"
                + "\n".join(morceaux) + "\n")

    def historique(self, arg=""):
        """Échanges conservés : étape en cours, une étape donnée (ex. D3) ou « tout »."""
        arg = arg.strip().upper()
        if arg in ("TOUT", "ALL"):
            codes = [e["code"] for e in self.etapes]
        elif arg and arg in [e["code"] for e in self.etapes]:
            codes = [arg]
        else:
            codes = [self.code]
        sortie = [f"📜 Historique — {self.titre}"]
        vide = True
        for code in codes:
            entrees = self.fil.get(code, [])
            if not entrees:
                continue
            vide = False
            titre = next(e["titre"] for e in self.etapes if e["code"] == code)
            sortie.append(f"\n── {code} · {titre} ──")
            for e in entrees:
                sortie.append(f"{LIB_FIL.get(e['r'], '•')} ({e['d']}) :\n{coupe(e['t'], 1200)}")
        if vide:
            sortie.append("\n(aucun échange enregistré sur cette étape)")
        sortie.append("\n/historique <étape> (ex. D3) ou /historique tout")
        return "\n".join(sortie)

    def reprise_msgs(self):
        """Reprise après une interruption (relance du programme ou du bot) : rappelle l'étape et pose
        de nouveau la question en attente, sans rien avoir à taper."""
        if not self.methode or self.fini:
            return []
        entete = f"▶️ On reprend à l'étape {self.idx + 1}/{len(self.etapes)} — {self.code} · {self.etape['titre']}"
        if not self.guide_actif():
            texte = f"{entete}\n\n{self.invite_texte()}"
            if self.brouillon:
                texte += f"\n\n🧾 Votre réponse en cours :\n{self.brouillon}"
            return [msg(texte, BTN_ETAPE)]
        st = self.qr.get(self.code) or {}
        if not st.get("r") and not st.get("c"):             # étape à peine commencée
            return [entete] + self.invite_msgs()
        sortie = [entete]
        if st.get("rel") is not None:                       # une relance de l'IA attendait une réponse
            relances = [e for e in self.fil.get(self.code, []) if e["r"] == "c"]
            if relances:
                sortie.append("🔎 " + relances[-1]["t"])
        return sortie + [self.question_msg()]

    def derniers_echanges(self, n=4):
        entrees = [e for e in self.fil.get(self.code, []) if e["r"] != "s"][-n:]
        if not entrees:
            return ""
        lignes = [f"💬 Derniers échanges sur {self.code} :"]
        lignes += [f"{LIB_FIL.get(e['r'], '•')} : {coupe(e['t'], 450)}" for e in entrees]
        return "\n\n".join(lignes)

    # -- appels LLM ------------------------------------------------------------
    def appeler_llm(self, prompt, max_tokens=None):
        msgs = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt}]
        try:
            texte, utilise = llm_texte(msgs, self.backend, self.modele("texte"), max_tokens=max_tokens)
        except Exception as e:
            return msg_erreur(e, self.backend)
        self.dernier_modele = utilise
        if utilise != self.modele("texte"):
            texte += f"\n\n↪️ (quota atteint : réponse donnée par {utilise})"
        return texte

    @staticmethod
    def lire_verdict(texte):
        """Sépare la ligne « VERDICT : SUFFISANT / À COMPLÉTER » du corps. -> (True|False|None, corps)"""
        verdict, corps = None, []
        for ligne in texte.splitlines():
            m = re.match(r"\W*VERDICT\s*:\s*(.+)", ligne, re.I)
            if m and verdict is None:
                v = m.group(1).upper()
                verdict = ("COMPL" not in v and "INSUFF" not in v) and "SUFFISANT" in v
                continue
            corps.append(ligne)
        return verdict, "\n".join(corps).strip()

    def coach_feedback(self):
        """Mode libre : retour sur la réponse cumulée de l'étape."""
        prompt = (
            self.entete() + self.photos_etape() + self.fil_pour_llm() +
            f"\nRéponse de l'utilisateur pour cette étape (cumul de ses messages) :\n{self.brouillon}\n\n"
            "Première ligne EXACTEMENT « VERDICT : SUFFISANT » (la réponse couvre l'attendu et les points "
            "à couvrir) ou « VERDICT : À COMPLÉTER ». Ensuite : ce qui est solide (1 point), ce qui "
            "manque (3 points max, en te référant aux points à couvrir) et, si à compléter, UNE seule "
            "question NOUVELLE — ne repose jamais une question déjà posée dans les échanges précédents."
        )
        retour = self.appeler_llm(prompt)
        verdict, corps = self.lire_verdict(retour)
        if verdict is None:
            return retour
        tete = "✅ Suffisant — validez avec /ok." if verdict else "📝 À compléter :"
        return f"{tete}\n\n{corps}"

    def verifier(self, question, reponse):
        """Mode guidé : relance courte si la réponse est floue ; '' si elle est suffisante."""
        lib, q, _ = question
        deja = self.assembler(sauf=self.qetat()["i"]) or "(rien)"
        prompt = (
            f"Méthode : {METHODES[self.methode]['nom']}. Problème : {self.titre}. "
            f"Étape {self.code} {self.etape['titre']}.\nRéponses déjà données sur cette étape :\n{deja}\n\n"
            f"Question posée : {q}\nRéponse de l'utilisateur : {reponse}\n\n"
            "Évalue UNIQUEMENT cette réponse. Si elle est factuelle et répond à la question (ou si "
            "l'utilisateur indique ne pas savoir), réponds exactement : OK\n"
            "Sinon, réponds par UNE seule question de relance courte (30 mots maximum) qui demande "
            "l'information, le fait ou le chiffre manquant. Pas de conseil, pas de reformulation, pas de liste."
        )
        self.erreur_llm = None
        try:
            texte, utilise = llm_texte([{"role": "system", "content": SYSTEM_PROMPT},
                                        {"role": "user", "content": prompt}],
                                       self.backend, self.modele("texte"), max_tokens=400)
        except Exception as e:
            self.erreur_llm = msg_erreur(e, self.backend)
            return ""
        self.dernier_modele = utilise
        lignes = [l.strip() for l in texte.strip().splitlines() if l.strip()]
        if not lignes or re.match(r"\W*OK\b", lignes[0], re.I):
            return ""
        relance = re.sub(r"^(relance|question)\s*:\s*", "", " ".join(lignes[:2]), flags=re.I)
        return coupe(relance, 300)

    def revue_etape(self):
        """Fin des questions : revue brève par rapport à l'attendu (mode guidé), sinon simple invite."""
        if self.mode != "guide":
            return msg("✅ Toutes les questions de l'étape sont traitées. Relisez vos réponses "
                       "(/reponse), puis validez.", BTN_FIN_QUESTIONS)
        prompt = (
            self.entete() + self.photos_etape() +
            f"\nRéponses de l'utilisateur à cette étape (question par question) :\n{coupe(self.brouillon, 3000)}\n\n"
            "Fais une revue brève de l'étape par rapport à l'attendu (80 mots maximum). Première ligne "
            "EXACTEMENT « VERDICT : SUFFISANT » ou « VERDICT : À COMPLÉTER ». Ensuite, au plus 2 manques "
            "précis (faits, chiffres, preuves absents). Ne pose pas de question et ne reformule pas."
        )
        retour = self.appeler_llm(prompt, max_tokens=700)
        verdict, corps = self.lire_verdict(retour)
        if verdict is None:
            texte = retour
        else:
            tete = ("✅ Revue de l'étape : suffisant." if verdict else "📝 Revue de l'étape : à compléter.")
            texte = f"{tete}\n{corps}\n\nValidez avec /ok, ou envoyez un complément."
        self.noter("c", texte)
        return msg(texte, BTN_FIN_QUESTIONS)

    def repondre_guide(self, t):
        """Mode guidé : t est la réponse à la question en cours. Retourne la liste des messages."""
        if self.rca_actif():
            return self.rca_repondre(t)
        st = self.qetat()
        qs = self.questions()
        i = st["i"]
        if i >= len(qs):                                   # toutes traitées : complément libre
            st["c"] = (st.get("c", "") + "\n" + t).strip()
            self.noter("u", f"[Complément] {t}")
            self.synchroniser()
            self.save()
            return [msg("📝 Ajouté à votre réponse. Validez l'étape quand vous êtes prêt(e).",
                        BTN_FIN_QUESTIONS)]
        self.noter("u", f"[{qs[i][0]}] {t}")
        if st.get("rel") == i:                             # réponse à une relance : on complète, on avance
            st["r"][str(i)] = (st["r"].get(str(i), "") + "\n" + t).strip()
            st["rel"] = None
        else:
            st["r"][str(i)] = t
            if self.mode == "guide" and "N'est pas" not in qs[i][0] \
                    and not RE_REPONSE_NEUTRE.fullmatch(t.strip()):
                relance = self.verifier(qs[i], t)
                if self.erreur_llm:                        # l'IA est indisponible : la réponse est gardée,
                    self.synchroniser()                    # on enchaîne sans relance (pas de blocage)
                    self.save()
                    return [self.erreur_llm + "\n(Votre réponse est enregistrée ; pas de relance IA pour "
                            "cette question. /mode rapide pour continuer sans IA.)"] + self.avancer_question()
                if relance:
                    st["rel"] = i
                    self.noter("c", relance)
                    self.synchroniser()
                    self.save()
                    return [msg("🔎 " + relance, [[("➡️ Question suivante", "/sauter"),
                                                   ("↩️ Précédente", "/prec")]])]
        sortie = []
        if self.methode == "8d" and self.idx == 0 and i == 0 and self.titre == "Sans titre":
            reponse = st["r"][str(i)].strip()
            if "\n" not in reponse and len(reponse) <= 90 and len(reponse.split()) <= 14:   # ressemble à un titre
                self.titre_propose = reponse
                sortie.append(msg(f"✏️ Titre proposé : « {self.titre_propose} »",
                                  [[("✅ Adopter ce titre", "/titre ok"), ("✏️ Un autre", "/titre")]]))
        return sortie + self.avancer_question()

    def avancer_question(self):
        st = self.qetat()
        st["i"] += 1
        st["rel"] = None
        self.synchroniser()
        self.save()
        if st["i"] >= len(self.questions()):
            sortie = [self.revue_etape()]
            self.save()
            return sortie
        return [self.question_msg()]

    # -- D5 : Ishikawa 6M → FTA → 5 Pourquoi -----------------------------------------
    def rca_step(self):
        return self.methode == "8d" and self.code == "D5" and self.guide_actif() and not self.fini

    def rca_actif(self):
        return self.rca_step() and self.qetat()["i"] == 0

    def rca_etat(self):
        return rca_calcul(self.rca["ev"])

    def rca_prompt(self, S=None, complet=True):
        """Question suivante de l'analyse. complet=False (enchaînement) : en FTA, la cause et la consigne
        ne sont rappelées qu'à la 1re des 3 questions ; /guide ou une reprise remettent tout le contexte."""
        S = S or self.rca_etat()
        if S["ph"] == "ish":
            d, (nom, desc) = DIMENSIONS[S["dim"]], SIX_M[S["m"]]
            n = 1 + sum(1 for c in S["causes"] if c["dim"] == S["dim"] and c["m"] == S["m"])
            cid = f"{d[0]}{S['m'] + 1}.{n}"
            tete = f"🐟 Ishikawa {d[1]} — facteur {S['m'] + 1}/{len(SIX_M)} : {nom}"
            if n == 1:
                corps = (f"{nom} : {desc}.\n{d[2]} à cause de ce facteur ? Écrivez UNE cause "
                         f"(n° {cid}), ou « - » s'il n'y en a aucune.")
            else:
                corps = f"Une autre cause {nom} ({cid}) ? Écrivez-la, ou « - » s'il n'y en a plus."
            return msg(f"{tete}\n{corps}", BTN_RCA_ISH)
        if S["ph"] == "fta":
            c = S["causes"][S["fi"]]
            lib, q = FTA_QUESTIONS[S["fq"]]
            question = f"Question {S['fq'] + 1}/{len(FTA_QUESTIONS)} — {lib}\n{q}"
            if S["fq"] == 0 or complet:
                return msg(f"🌳 FTA — cause {rca_lib(c)} : « {c['t']} » ({S['fi'] + 1}/{len(S['causes'])})\n"
                           f"{question}\nRépondez par oui ou non (un commentaire peut suivre).", BTN_RCA_FTA)
            return msg(question, BTN_RCA_FTA)
        if S["ph"] == "pq":
            cid = S["prouvees"][S["pi"]]
            c = next(x for x in S["causes"] if x["id"] == cid)
            w = S["why"].get(cid, [])
            n = len(w) + 1
            tete = (f"❓ 5 Pourquoi — cause prouvée {rca_lib(c)} : « {c['t']} » "
                    f"({S['pi'] + 1}/{len(S['prouvees'])})")
            if n == 1:
                corps = "Pourquoi 1 : pourquoi cette cause est-elle présente ?"
            else:
                corps = f"Pourquoi {n} : pourquoi « {coupe(w[-1], 140)} » ?"
                if n > POURQUOI_MIN:
                    corps += ("\nContinuez tant que vous n'avez pas atteint la cause racine ; "
                              "« - » (ou /fin) quand la dernière réponse est la cause racine.")
            return msg(f"{tete}\n{corps}", BTN_RCA_PQ)
        return msg("🏁 Analyse des causes terminée. /ok pour valider l'étape, /prec pour corriger.",
                   BTN_FIN_QUESTIONS)

    def rca_sync(self, S):
        st = self.qetat()
        st["r"]["0"] = rca_resume(S)
        self.synchroniser()
        self.save()

    def rca_suite(self, A, avant=()):
        """Après un événement : transitions de phase, synchro de la réponse D5, question suivante."""
        B = self.rca_etat()
        out = list(avant)
        if B["dim"] > A["dim"]:
            out.append(rca_diagramme(B, A["dim"]))
        if A["ph"] == "ish" and B["ph"] != "ish":
            n = len(B["causes"])
            out.append(f"🌳 FTA : {n} cause{'s' if n > 1 else ''} à examiner. Pour chacune : un standard "
                       "existe-t-il, est-il appliqué, est-il robuste ? Dès qu'une réponse est « non », la "
                       "cause est retenue comme prouvée." if n else
                       "Aucune cause saisie : il n'y a rien à analyser avec le FTA.")
        if A["ph"] == "fta" and B["ph"] != "fta":
            p = [c for c in B["causes"] if c["id"] in B["prouvees"]]
            out.append(f"🌳 FTA terminé : {len(p)} cause{'s' if len(p) > 1 else ''} prouvée"
                       f"{'s' if len(p) > 1 else ''} sur {len(B['causes'])}."
                       + ("\n" + "\n".join(f"• {rca_lib(c)} : {c['t']}" for c in p) if p else "")
                       + ("\nLes 5 Pourquoi suivent, cause par cause." if p else ""))
        self.rca_sync(B)
        if B["ph"] == "fin":
            out.append("🏁 Analyse des causes terminée.")
            return out + self.avancer_question()
        return out + [self.rca_prompt(B, complet=False)]

    def rca_repondre(self, t):
        A = self.rca_etat()
        ev = self.rca["ev"]
        if A["ph"] == "ish":
            if t.lower().strip(" .!") in FIN_SAISIE:
                return self.rca_commande("sauter")
            ev.append(["c", t])
            n = 1 + sum(1 for c in A["causes"] if c["dim"] == A["dim"] and c["m"] == A["m"])
            cid = f"{DIMENSIONS[A['dim']][0]}{A['m'] + 1}.{n}"
            self.noter("u", f"[Cause {cid}] {t}")
            return self.rca_suite(A, [f"✅ {cid} enregistrée."])
        if A["ph"] == "fta":
            m = RE_OUINON.match(t)
            if not m:
                return [msg("Répondez par oui ou non (vous pouvez ajouter un commentaire après).",
                            BTN_RCA_FTA)]
            return self.rca_oui_non("oui" if m.group(1).lower() in ("oui", "o", "yes", "y") else "non",
                                    m.group(2).strip())
        if A["ph"] == "pq":
            if t.lower().strip(" .!") in ("-", "fin", "terminé", "termine", "fini", "racine"):
                return self.rca_commande("fin")
            cid = A["prouvees"][A["pi"]]
            n = len(A["why"].get(cid, [])) + 1
            ev.append(["p", t])
            self._pq_alerte = None
            self.noter("u", f"[Pourquoi {n} · {cid}] {t}")
            return self.rca_suite(A)
        return [self.rca_prompt(A)]

    def rca_oui_non(self, rep, note=""):
        A = self.rca_etat()
        if A["ph"] != "fta":
            return ["« /oui » et « /non » répondent aux questions du FTA (étape D5 du 8D)."]
        c = A["causes"][A["fi"]]
        lib = FTA_QUESTIONS[A["fq"]][0]
        self.rca["ev"].append(["f", rep, note])
        self.noter("u", f"[FTA {c['id']} · {lib}] {rep}" + (f" — {note}" if note else ""))
        if rep == "non":
            info = [f"🔴 {c['id']} : « {lib.lower()} » = non → cause PROUVÉE (5 Pourquoi à suivre)."]
        elif A["fq"] + 1 >= len(FTA_QUESTIONS):
            info = [f"🟢 {c['id']} : standard existant, appliqué et robuste → cause écartée."]
        else:
            info = []
        return self.rca_suite(A, info)

    def rca_commande(self, nom):
        """/sauter, /fin (ou « - ») pendant l'analyse : fin du facteur, cause non évaluée, fin des pourquoi."""
        A = self.rca_etat()
        ev = self.rca["ev"]
        if A["ph"] == "ish":
            self.noter("s", f"Facteur {SIX_M[A['m']][0]} ({DIMENSIONS[A['dim']][1]}) terminé.")
            ev.append(["m"])
            return self.rca_suite(A)
        if A["ph"] == "fta":
            if nom == "fin":
                return ["« /fin » termine un facteur ou les pourquoi ; ici répondez oui ou non "
                        "(/sauter laisse la cause non évaluée)."]
            c = A["causes"][A["fi"]]
            self.noter("s", f"Cause {c['id']} non évaluée.")
            ev.append(["fs"])
            return self.rca_suite(A, [f"⏭️ {c['id']} laissée non évaluée."])
        if A["ph"] == "pq":
            cid = A["prouvees"][A["pi"]]
            w = A["why"].get(cid, [])
            if not w:
                return [msg("Écrivez au moins un pourquoi.", BTN_RCA_PQ)]
            if len(w) < POURQUOI_MIN and getattr(self, "_pq_alerte", None) != cid:
                self._pq_alerte = cid
                return [msg(f"⚠️ {len(w)} pourquoi seulement ({POURQUOI_MIN} au minimum sont attendus). "
                            "Êtes-vous bien à la cause racine ? Répétez « - » (ou /fin) pour confirmer, "
                            "sinon continuez à répondre.", BTN_RCA_PQ)]
            self._pq_alerte = None
            ev.append(["pe"])
            self.noter("s", f"Cause racine de {cid} : {w[-1]}")
            return self.rca_suite(A, [f"🏁 Cause racine de {cid} : « {coupe(w[-1], 200)} »."])
        return [self.rca_prompt(A)]

    def rca_prec(self):
        st = self.qetat()
        if st["i"] >= 1:                                   # analyse terminée : on la ré-ouvre
            st["i"] = 0
        ev = self.rca["ev"]
        if not ev:
            self.save()
            return [msg("Rien à annuler : l'analyse n'a pas encore commencé.", self.rca_prompt()["boutons"])]
        derniere = ev.pop()
        self._pq_alerte = None
        self.noter("s", "Dernière saisie annulée.")
        B = self.rca_etat()
        self.rca_sync(B)
        quoi = {"c": "la dernière cause", "m": "la fin du facteur", "f": "la dernière réponse FTA",
                "fs": "le saut de la cause", "p": "le dernier pourquoi", "pe": "la fin des pourquoi"}[derniere[0]]
        return [f"↩️ Annulé : {quoi}.", self.rca_prompt(B)]

    def coach_question(self, question):
        prompt = (self.entete() + self.fil_pour_llm() +
                  (f"Question actuellement posée à l'utilisateur : {self.questions()[self.qetat()['i']][1]}\n"
                   if self.guide_actif() and self.questions_restantes() else "") +
                  (f"\nRéponse en cours de l'utilisateur :\n{self.brouillon}\n" if self.brouillon else "") +
                  f"\nQuestion de l'utilisateur : {question}\n"
                  "Réponds de façon pédagogique, en termes généraux, sans exemple chiffré ni propre à un secteur.")
        return self.appeler_llm(prompt)

    def proposer_titre(self):
        """Propose un titre court d'après la première étape validée ; '' si indisponible."""
        e = self.etapes[0]
        rep = self.reponses.get(e["code"], "")
        if not rep:
            return ""
        try:
            texte, _ = llm_texte(
                [{"role": "system", "content": SYSTEM_PROMPT},
                 {"role": "user", "content":
                  f"Méthode : {METHODES[self.methode]['nom']}. Étape {e['code']} {e['titre']} :\n{rep}\n\n"
                  "Propose UN titre factuel de 10 mots maximum pour ce problème (produit/processus + "
                  "écart constaté). Réponds uniquement par le titre, sans guillemets."}],
                self.backend, self.modele("texte"))
        except Exception:
            return ""
        titre = texte.strip().splitlines()[0].strip(" «»\"'*#")
        return coupe(titre, 90)

    # -- photos ----------------------------------------------------------------
    def analyser_image(self, donnees, legende=""):
        """Analyse une photo au regard de l'étape en cours. Retourne la liste des messages."""
        try:
            image = preparer_image(donnees)
        except Exception as e:
            return [f"⚠️ Image inutilisable : {e}"]

        question = (f"Légende / question de l'utilisateur : {legende}\n" if legende else "")
        en_cours = bool(self.methode and not self.fini)
        if en_cours:
            contexte = self.entete()
            demande = "2. 🔎 Ce que cela suggère pour cette étape (formulé comme hypothèse à vérifier)\n"
        else:
            contexte = "Aucune démarche de résolution de problème n'est en cours.\n"
            demande = ("2. 🧭 La méthode la plus adaptée parmi 8D, A3, PDCA, Kaizen 6 points, "
                       "et un énoncé de problème provisoire, strictement factuel\n")
        prompt = (
            contexte + question +
            "Une photo est jointe. Réponds en français, 170 mots maximum, en 4 parties courtes :\n"
            "1. 👁️ Ce qui est visible (faits observables uniquement, sans interprétation ; "
            "texte et chiffres lisibles)\n" + demande +
            "3. ⚠️ Ce qu'on ne peut PAS conclure à partir d'une photo\n"
            "4. ❓ 2 vérifications ou mesures à faire sur le terrain"
        )
        try:
            texte = llm_vision(prompt, image, self.backend,
                               self.modele("vision"), self.modele("texte"))
        except Exception as e:
            return [msg_erreur(e, self.backend)]

        if en_cours:
            code = self.code
            date = datetime.now().strftime("%d/%m/%Y %H:%M")
            self.photos.setdefault(code, []).append({"legende": legende, "analyse": texte, "date": date})
            self.noter("p", (f"[{legende}] " if legende else "") + texte)
            self.save()
            return [f"📷 Analyse de la photo (étape {code}) :\n{texte}",
                    msg("💾 Analyse enregistrée avec l'étape (elle figurera dans le rapport). "
                        "Ajoutez vos propres constats, puis validez.", BTN_RETOUR_COACH)]
        return [f"📷 Analyse de la photo :\n{texte}",
                msg("Pour structurer la démarche :", BTN_DEMARRER[:2])]

    # -- projet : cycle de vie ---------------------------------------------------
    def demarrer(self, cle, titre):
        precedent = self.methode
        self._vider_projet()
        self.purger_vides()                     # le projet précédent, s'il est resté vide, disparaît
        self.methode = cle
        self.titre = titre or "Sans titre"
        self.debut = datetime.now().strftime("%d/%m/%Y %H:%M")
        self.pid = f"{datetime.now():%Y%m%d-%H%M%S}_{cle}"
        self.attend_titre = False
        self.save()
        m = METHODES[cle]
        entete = (f"🚀 Démarrage {m['nom']} — « {self.titre} »\n"
                  f"🤖 {self.backend} · {libelle_modele(self.backend, 'texte', self.modele('texte'))}\n\n"
                  f"{m['principe']}")
        entete += f"\n\n🧭 Mode {MODES[self.mode]} (/mode pour changer)."
        if self.titre == "Sans titre":
            entete += "\n✏️ Titre : définissable à tout moment (/titre) ; il vous sera aussi proposé."
        if precedent:
            entete += "\n💾 Le projet précédent est conservé (/projets)."
        return [entete] + self.invite_msgs()

    def valider(self, force=False):
        self.synchroniser()
        if not self.brouillon:
            return ["Rien à valider : répondez à au moins une question de l'étape "
                    "(l'analyse d'une photo ne remplace pas vos propres constats)."
                    if self.guide_actif() else
                    "Rien à valider : rédigez votre réponse à l'étape "
                    "(l'analyse d'une photo ne remplace pas vos propres constats)."], None
        if self.rca_actif() and not force:
            return [msg("⚠️ L'analyse des causes (Ishikawa, FTA, 5 Pourquoi) n'est pas terminée. "
                        "Valider quand même ?",
                        [[("✅ Valider quand même", "/ok force"), ("▶️ Continuer", "/guide")]])], None
        if self.guide_actif() and self.questions_restantes() and not force:
            n = self.questions_restantes()
            return [msg(f"⚠️ Il reste {n} question{'s' if n > 1 else ''} dans cette étape. "
                        "Valider quand même ?",
                        [[("✅ Valider quand même", "/ok force"), ("▶️ Continuer les questions", "/guide")]])], None
        self.reponses[self.code] = self.brouillon
        self.noter("s", "Étape validée.")
        premier = self.idx == 0
        msgs, fichier = self.avancer()
        if premier and fichier is None and self.titre == "Sans titre" and not self.titre_propose:
            propose = self.proposer_titre()
            if propose:
                self.titre_propose = propose
                self.save()
                msgs.insert(1, msg(f"✏️ Titre proposé : « {propose} »",
                                   [[("✅ Adopter ce titre", "/titre ok"), ("✏️ Un autre", "/titre")]]))
        return msgs, fichier

    def avancer(self):
        self.brouillon = ""
        self.idx += 1
        if self.idx >= len(self.etapes):
            return self.terminer()
        self.save()
        return ["✔️ Étape validée."] + self.invite_msgs(), None

    def terminer(self):
        self.fini = True
        self.idx = len(self.etapes) - 1
        self.save()
        m = METHODES[self.methode]
        revue = self.appeler_llm(
            f"Méthode : {m['nom']}. {m['note']}\nProblème : {self.titre}.\n"
            f"Démarche complète :\n{self.contexte()}\n\n"
            "Fais une revue de cohérence finale : le problème est-il bien posé, les causes ou "
            "principes retenus sont-ils traités par les actions, les résultats sont-ils prouvés ? "
            "Que manque-t-il ? Donne 3 à 5 points concis."
        )
        self.noter("c", "Revue de cohérence finale :\n" + revue)
        self.save()
        chemin = self.rapport(revue)
        return ["🎉 Démarche terminée !", f"🔎 Revue de cohérence :\n{revue}",
                msg(f"📄 Rapport enregistré : {chemin.name}", BTN_FIN)], chemin

    def rapport(self, revue="", complet=False):
        nom = METHODES[self.methode]["nom"]
        lignes = [f"# {nom} — {self.titre}", "",
                  f"*Démarré le {self.debut} — généré le "
                  f"{datetime.now().strftime('%d/%m/%Y %H:%M')}*", ""]
        for e in self.etapes:
            code = e["code"]
            texte = self.reponses.get(code)
            if not texte and code == self.code and code not in self.reponses and self.brouillon \
                    and not self.fini:
                texte = self.brouillon + "\n\n_(réponse en cours, non validée)_"
            lignes += [f"## {code} — {e['titre']}", "", texte or "_Non renseigné_", ""]
            for p in self.photos.get(code, []):
                leg = f" — {p['legende']}" if p.get("legende") else ""
                lignes += [f"> 📷 **Photo{leg}** ({p.get('date', '')}) — analyse IA, "
                           "à vérifier sur le terrain", ">"]
                lignes += ["> " + ligne for ligne in p["analyse"].splitlines()] + [""]
        if revue:
            lignes += ["## Revue de cohérence (IA)", "", revue, ""]
        if complet:
            lignes += ["---", "", "# Annexe — échanges complets", ""]
            for e in self.etapes:
                entrees = [x for x in self.fil.get(e["code"], []) if x["r"] != "s"]
                if not entrees:
                    continue
                lignes += [f"## {e['code']} — {e['titre']}", ""]
                for x in entrees:
                    lignes += [f"**{LIB_FIL.get(x['r'], '•')}** ({x['d']})", "", x["t"], ""]
        suffixe = "_complet" if complet else ""
        chemin = REPORT_DIR / f"{self.methode}_{self.uid}_{datetime.now():%Y%m%d_%H%M%S}{suffixe}.md"
        chemin.write_text("\n".join(lignes), encoding="utf-8")
        return chemin

    def etat(self):
        if not self.methode:
            return msg("Aucune démarche en cours.", BTN_DEMARRER)
        out = [f"{METHODES[self.methode]['nom']} — {self.titre}",
               f"🤖 {self.backend} · {libelle_modele(self.backend, 'texte', self.modele('texte'))} "
               f"· 📷 {libelle_modele(self.backend, 'vision', self.modele('vision'))}"]
        for i, e in enumerate(self.etapes):
            code = e["code"]
            if code in self.reponses:
                marque = "✅" if self.reponses[code] else "⏭️"
            elif i == self.idx and not self.fini:
                marque = "👉"
            else:
                marque = "▫️"
            nb_p = len(self.photos.get(code, []))
            nb_e = len([x for x in self.fil.get(code, []) if x["r"] in ("u", "q")])
            extra = (f"  📷×{nb_p}" if nb_p else "") + (f"  💬{nb_e}" if nb_e else "")
            out.append(f"{marque} {code} {e['titre']}{extra}")
        if self.guide_actif() and not self.fini:
            n = len(self.questions())
            out.append(f"\n❓ Question {min(self.qetat()['i'] + 1, n)}/{n} de l'étape {self.code} "
                       f"· mode {self.mode}")
        if self.brouillon and not self.fini:
            out.append(f"✍️ Réponse en cours ({len(self.brouillon)} caractères) — /reponse pour la relire")
        return msg("\n".join(out), BTN_ETAPE if not self.fini else BTN_FIN)

    # -- projets : liste, chargement, titre ---------------------------------------
    def projets(self):
        """[(pid, données)] du plus récent au plus ancien."""
        sortie = []
        for p in sorted(self.dir.glob("*.json"), reverse=True):
            if p.name.startswith("_"):
                continue
            d = lire_json(p)
            if isinstance(d, dict) and d.get("methode") in METHODES:
                sortie.append((p.stem, d))
        return sortie

    @staticmethod
    def _resume_projet(d):
        m = METHODES[d["methode"]]
        total = len(m["etapes"])
        if d.get("fini"):
            avance = "terminé ✅"
        else:
            avance = f"étape {min(int(d.get('idx', 0)) + 1, total)}/{total}"
        nb = sum(len([x for x in v if x["r"] in ("u", "q")]) for v in (d.get("fil") or {}).values())
        return f"[{m['nom']}] {d.get('titre', 'Sans titre')} — {avance} · 💬{nb} · {d.get('maj', '')}"

    def liste_projets(self):
        projets = self.projets()
        if not projets:
            return msg("📭 Aucun projet enregistré. Démarrez avec /8d, /a3, /pdca ou /k6pts.", BTN_DEMARRER)
        lignes = ["📂 Vos projets (à partir du plus récent) :"]
        boutons = []
        for i, (pid, d) in enumerate(projets[:10], 1):
            actif = "▶️ " if pid == self.pid else ""
            lignes.append(f"{i}. {actif}{self._resume_projet(d)}")
            lib = f"{'▶️ ' if pid == self.pid else ''}{i}. {coupe(d.get('titre', 'Sans titre'), 28)} ({METHODES[d['methode']]['nom']})"
            boutons.append([(lib, f"/charger {pid}")])
        lignes.append("\n/charger <n> pour ré-ouvrir · /supprimer <n> pour effacer")
        return msg("\n".join(lignes), boutons, remplace=True)

    def _resoudre_projet(self, arg):
        arg = arg.strip()
        projets = self.projets()
        if arg.isdigit() and 1 <= int(arg) <= len(projets):
            return projets[int(arg) - 1][0]
        return arg if arg in [p for p, _ in projets] else None

    def charger(self, arg):
        if not arg.strip():
            return [self.liste_projets()]
        pid = self._resoudre_projet(arg)
        if not pid:
            return [msg("❌ Projet introuvable.", [[("📂 Projets", "/projets")]])]
        if self.pid and self.methode:
            self.save()                      # rien ne se perd en changeant de projet
        if not self.ouvrir(pid):
            return ["❌ Projet illisible."]
        self.attend_titre = False
        self.save()
        sortie = [f"📂 Projet rechargé : « {self.titre} » ({METHODES[self.methode]['nom']}) — "
                  f"{'terminé ✅' if self.fini else f'étape {self.idx + 1}/{len(self.etapes)}'}",
                  self.etat()]
        if not self.fini:
            if self.brouillon:
                sortie.append(f"✍️ Réponses déjà données sur {self.code} :\n{self.brouillon}")
            echanges = self.derniers_echanges()
            if echanges:
                sortie.append(echanges)
            sortie += self.invite_msgs()
        return sortie

    def sauver_projet(self, arg):
        if arg.strip():
            self.titre = coupe(arg, 120)
        self.save()
        nb = sum(len(v) for v in self.fil.values())
        return [msg(f"💾 Projet enregistré : « {self.titre} » — {METHODES[self.methode]['nom']}, "
                    f"étape {self.idx + 1}/{len(self.etapes)} · {nb} échanges conservés.\n"
                    "Il est rechargeable à tout moment avec /projets (et sauvegardé "
                    "automatiquement à chaque message).", [[("📂 Projets", "/projets")]])]

    def definir_titre(self, texte):
        self.attend_titre = False
        t = coupe(texte.strip().strip("«»\"'"), 120)
        if not t or t == "-":
            return [msg(f"Titre inchangé : « {self.titre} »")]
        self.titre = t
        self.save()
        return [f"✏️ Titre : « {self.titre} »"]

    def cmd_titre(self, arg):
        arg = arg.strip()
        if arg.lower() == "ok":
            if not self.titre_propose:
                return ["Aucun titre proposé pour l'instant. /titre <texte> pour en définir un."]
            return self.definir_titre(self.titre_propose)
        if arg:
            return self.definir_titre(arg)
        self.attend_titre = True
        return [f"✏️ Titre actuel : « {self.titre} »\nEnvoyez le nouveau titre dans votre prochain "
                "message (ou « - » pour ne rien changer)."]

    def fermer(self):
        if not self.methode:
            return [msg("Aucun projet ouvert.", BTN_DEMARRER)]
        titre = self.titre
        self.save()
        vide = self.projet_vide(self._donnees_projet())
        self._vider_projet()
        self.purger_vides()
        self.attend_titre = False
        self.prefs["actif"] = None
        self.sauver_prefs()
        if vide:
            return [msg("📦 Projet fermé (il était vide : rien à conserver).", BTN_DEMARRER)]
        return [msg(f"📦 Projet « {titre} » mis de côté (conservé). /projets pour le ré-ouvrir.",
                    BTN_DEMARRER)]

    def supprimer(self, arg):
        mots = arg.split()
        if mots and mots[0].lower() == "oui":            # confirmation : /supprimer oui <pid>
            pid = self._resoudre_projet(" ".join(mots[1:]))
            if not pid:
                return ["❌ Projet introuvable."]
            (self.dir / f"{pid}.json").unlink(missing_ok=True)
            if pid == self.pid:
                self._vider_projet()
                self.prefs["actif"] = None
                self.sauver_prefs()
            return [msg("🗑 Projet supprimé.", [[("📂 Projets", "/projets")]])]
        pid = self._resoudre_projet(arg) if arg.strip() else self.pid
        if not pid:
            return [msg("Indiquez le projet à supprimer (/projets pour la liste) : /supprimer <n>")]
        d = lire_json(self.dir / f"{pid}.json", {}) or {}
        return [msg(f"🗑 Supprimer définitivement « {d.get('titre', pid)} » ?",
                    [[("🗑 Oui, supprimer", f"/supprimer oui {pid}"), ("Annuler", "/projets")]])]

    # -- moteur et modèles ---------------------------------------------------------
    def _choisir(self, liste, x):
        x = x.strip()
        if x.isdigit() and 1 <= int(x) <= len(liste):
            return liste[int(x) - 1][0]
        for mid, lib, _ in liste:
            if x.lower() in (mid.lower(), lib.lower()):
                return mid
        return None

    def menu_modeles(self, prefixe=""):
        b = self.backend
        texte_actif, vision_actif = self.modele("texte"), self.modele("vision")
        lignes = [prefixe] if prefixe else []
        lignes += ["🤖 Moteur IA", f"   Moteur : {b}"]
        boutons = [[(("✅ " if b == "groq" else "") + "Groq (cloud)", "/modele b groq"),
                    (("✅ " if b == "ollama" else "") + "Ollama (Pi5)", "/modele b ollama")]]
        for usage, actif, icone, titre in (("texte", texte_actif, "🧠", "Texte"),
                                           ("vision", vision_actif, "📷", "Photos")):
            cat = catalogue(b, usage)
            lignes.append(f"\n{icone} {titre} : {libelle_modele(b, usage, actif)}")
            if not cat:
                lignes.append("   (aucun modèle détecté" + (" — Ollama est-il démarré ?)" if b == "ollama" else ")"))
            for i, (mid, lib, desc) in enumerate(cat, 1):
                lignes.append(f"   {i}. {'✅ ' if mid == actif else ''}{lib} — {desc}")
                cmd = f"/modele {'t' if usage == 'texte' else 'v'} {mid}"
                if len(cmd.encode()) <= 64:
                    boutons.append([(f"{icone} {'✅ ' if mid == actif else ''}{coupe(lib, 32)}", cmd)])
        lignes.append(f"\n🧭 Mode : {self.mode} — {MODES[self.mode]}")
        boutons.append([(("✅ " if self.mode == m else "") + lib, f"/mode {m}")
                        for m, lib in (("guide", "Guidé"), ("rapide", "Rapide"), ("libre", "Libre"))])
        lignes.append("\nTaper : /modele b groq|ollama   /modele t <n|nom>   /modele v <n|nom>   /mode guide|rapide|libre")
        boutons.append([("📊 État", "/etat")])
        return msg("\n".join(lignes), boutons, remplace=True)

    def cmd_modele(self, arg, nom="modele"):
        arg = arg.strip()
        if nom == "backend":                              # ancienne commande /backend groq|ollama
            arg = f"b {arg}" if arg else ""
        if not arg:
            return self.menu_modeles()
        mots = arg.split(None, 1)
        cle, reste = mots[0].lower(), (mots[1] if len(mots) > 1 else "")
        if cle in ("b", "moteur", "backend") or cle in ("groq", "ollama"):
            valeur = (reste or cle).lower().strip()
            if valeur not in ("groq", "ollama"):
                return self.menu_modeles("⚠️ Moteur inconnu : groq ou ollama.")
            self.prefs["backend_choisi"] = valeur
            self.sauver_prefs()
            return self.menu_modeles(f"✅ Moteur : {valeur}")
        usage = "vision" if cle in ("v", "vision", "photo", "photos") else "texte"
        if cle in ("t", "texte", "v", "vision", "photo", "photos"):
            choix = reste
        else:
            choix = arg                                   # /modele <n|nom> = modèle texte
        cat = catalogue(self.backend, usage)
        mid = self._choisir(cat, choix)
        if not mid and self.backend == "groq" and re.fullmatch(r"[\w.\-]+/[\w.\-]+", choix.strip()):
            mid = choix.strip()                           # modèle Groq absent de la liste (au choix)
        if not mid:
            return self.menu_modeles(f"⚠️ Modèle inconnu : « {choix} » (indiquez le numéro ou le nom).")
        self.prefs.setdefault("modeles", {}).setdefault(self.backend, {})[usage] = mid
        self.sauver_prefs()
        return self.menu_modeles(f"✅ {'Photos' if usage == 'vision' else 'Texte'} : {libelle_modele(self.backend, usage, mid)}")

    def cmd_mode(self, arg):
        valeur = arg.strip().lower().replace("guidé", "guide")
        if valeur not in MODES:
            return self.menu_modeles("⚠️ Mode inconnu : guide, rapide ou libre." if valeur else "")
        ancien = self.mode
        self.prefs["mode"] = valeur
        if self.methode and not self.fini:
            if valeur != "libre":
                st = self.qetat()                         # état des questions de l'étape en cours
                if ancien == "libre" and self.brouillon and not st["r"] and not st.get("c"):
                    st["c"] = self.brouillon              # le texte saisi en mode libre est conservé
            self.synchroniser()
        self.save() if self.pid else self.sauver_prefs()
        return self.menu_modeles(f"✅ Mode {valeur} : {MODES[valeur]}")

    # -- commandes -----------------------------------------------------------------
    def photo_locale(self, arg):
        """/photo <chemin> [légende] — terminal uniquement."""
        try:
            parts = shlex.split(arg)
        except ValueError:
            parts = arg.split()
        if not parts:
            return ["Usage : /photo <chemin_image> [légende]"]
        chemin = Path(parts[0]).expanduser()
        if chemin.suffix.lower() not in IMAGE_EXT or not chemin.is_file():
            return [f"Image introuvable ou format non géré : {chemin}"]
        return self.analyser_image(chemin.read_bytes(), " ".join(parts[1:]))

    def commande(self, cmd, arg, cli=False):
        nom = cmd[1:]
        if nom != "titre":
            self.attend_titre = False
        if nom in ("start", "aide", "help", "menu"):
            return [msg(aide_tableau() if cli else AIDE, BTN_DEMARRER)], None
        if nom in QUITTER:
            if cli:
                return [], None                           # le terminal quitte avant d'arriver ici
            return ["Cette commande ne sert qu'en terminal. Sur Telegram, /stop met le projet de côté "
                    "(il est conservé) ; l'arrêt du bot se fait depuis le menu du Pi5."], None
        cle = ALIAS_METHODES.get(nom, nom)
        if cle in METHODES:
            return self.demarrer(cle, arg), None
        if nom == "photo":
            if cli:
                return self.photo_locale(arg), None
            return ["Envoyez simplement une photo dans la conversation "
                    "(la légende sert de question)."], None
        if nom in ("modele", "model", "backend"):
            return [self.cmd_modele(arg, nom)], None
        if nom == "mode":
            return [self.cmd_mode(arg)], None
        if nom in ("projets", "projet", "sessions"):
            return [self.liste_projets()], None
        if nom in ("charger", "reprendre", "resume"):
            return self.charger(arg), None
        if nom == "supprimer":
            return self.supprimer(arg), None
        if nom in ("stop", "nouveau", "fermer"):
            return self.fermer(), None
        if nom == "etat":
            return [self.etat()], None
        if not self.methode:
            return [msg("Aucune démarche en cours : choisissez une méthode, ou ré-ouvrez un projet.",
                        BTN_DEMARRER)], None
        if nom == "titre":
            return self.cmd_titre(arg), None
        if nom == "sauver":
            return self.sauver_projet(arg), None
        if nom in ("historique", "histo"):
            return [self.historique(arg)], None
        if nom in ("guide", "etape", "question"):
            return self.invite_msgs() if not self.fini else [self.invite_etape()], None
        if nom == "reponse":
            self.synchroniser()
            if not self.brouillon:
                return ["Aucune réponse en cours sur cette étape."], None
            return [msg(f"🧾 Vos réponses ({self.code}) :\n{self.brouillon}",
                        BTN_FIN_QUESTIONS if self.guide_actif() else BTN_RETOUR_COACH)], None
        if nom == "rapport":
            chemin = self.rapport(complet=arg.strip().lower() in ("complet", "c", "full"))
            return [f"📄 Rapport (état actuel) : {chemin.name}"], chemin
        if self.fini:
            if nom == "retour":                            # rouvrir la dernière étape
                self.fini = False
                self.brouillon = self.reponses.pop(self.code, "")
                return self.reouvrir_etape("↩️ Démarche rouverte.")
            return [msg("Démarche terminée. /rapport complet, /retour pour ré-ouvrir la dernière étape, "
                        "ou une nouvelle démarche.", BTN_FIN)], None
        if nom in ("sauter", "fin") and self.rca_actif():
            return self.rca_commande(nom), None
        if nom in ("oui", "non") and self.rca_actif():
            return self.rca_oui_non(nom), None
        if nom in ("oui", "non", "fin"):
            return ["Cette commande ne sert que pendant l'analyse des causes (étape D5 du 8D)."], None
        if nom == "ok":
            return self.valider(force=arg.strip().lower() in ("force", "!"))
        if nom == "sauter":
            if not self.guide_actif():
                return ["« /sauter » concerne le mode guidé (/mode guide)."], None
            if not self.questions_restantes():
                return ["Toutes les questions de l'étape sont déjà traitées."], None
            self.noter("s", "Question sautée.")
            return self.avancer_question(), None
        if nom == "prec":
            if not self.guide_actif():
                return ["« /prec » concerne le mode guidé (/mode guide)."], None
            if self.rca_step():
                return self.rca_prec(), None
            st = self.qetat()
            if st["i"] <= 0:
                return ["Vous êtes déjà à la première question de l'étape."], None
            st["i"] = min(st["i"], len(self.questions())) - 1
            st["rel"] = None
            self.save()
            return [self.question_msg()], None
        if nom == "effacer":
            self.brouillon = ""
            self.qr[self.code] = {"i": 0, "r": {}, "rel": None}
            if self.methode == "8d" and self.code == "D5":
                self.rca = {"ev": []}
            self.noter("s", "Réponses de l'étape effacées.")
            self.save()
            return ["🧹 Réponses de l'étape effacées (l'historique de la conversation est conservé)."]\
                + (self.invite_msgs()[1:] if self.guide_actif() else []), None
        if nom == "passer":
            self.reponses[self.code] = ""
            self.noter("s", "Étape passée.")
            return self.avancer()
        if nom == "retour":
            if self.idx == 0:
                return ["Vous êtes déjà à la première étape."], None
            self.idx -= 1
            self.brouillon = self.reponses.pop(self.code, "")
            self.noter("s", "Retour à cette étape.")
            return self.reouvrir_etape("↩️ Retour à l'étape précédente.")
        return ["Commande inconnue. /aide pour la liste."], None

    def reouvrir_etape(self, titre):
        """Étape déjà validée rouverte (/retour) : ses réponses sont réaffichées."""
        if self.guide_actif():
            st = self.qetat()
            if not st["r"] and not st.get("c") and self.brouillon:   # étape saisie en mode libre
                st["c"] = self.brouillon
            st["i"] = len(self.questions())                          # tout est répondu : /ok ou /prec
            self.synchroniser()
        self.save()
        texte = f"{titre}\n\n{self.invite_texte()}"
        if self.brouillon:
            texte += f"\n\n🧾 Vos réponses :\n{self.brouillon}"
        if self.guide_actif():
            texte += "\n\n/ok pour revalider, /prec pour corriger une réponse."
            return [msg(texte, BTN_FIN_QUESTIONS)], None
        return [msg(texte, BTN_ETAPE)], None

    def handle(self, texte, cli=False):
        """Point d'entrée unique : retourne (liste_de_messages, fichier_ou_None).
        Un message est un str, ou un dict {texte, boutons, remplace} (voir msg())."""
        t = texte.strip()
        if not t:
            return [], None
        if t.startswith("/"):
            cmd, _, arg = t.partition(" ")
            return self.commande(cmd.lower().split("@")[0], arg.strip(), cli=cli)
        if self.attend_titre and self.methode:
            return self.definir_titre(t), None
        if not self.methode:
            return [msg("Choisissez une méthode (ou ré-ouvrez un projet avec /projets) :",
                        BTN_DEMARRER)], None
        if self.fini:
            return [msg("Démarche terminée. /rapport complet, /retour pour rouvrir la dernière "
                        "étape, ou une nouvelle démarche.", BTN_FIN)], None
        guide = self.guide_actif()
        if t.lower() in OK_WORDS and not (guide and self.questions_restantes()):
            return self.valider()                          # en mode guidé « oui » reste une réponse
        if guide and self.questions_restantes() and t.lower().strip(" .!") in ACQUIT:
            if self.rca_actif():
                if self.rca_etat()["ph"] != "ish":          # (en Ishikawa, « ok » termine le facteur)
                    return [self.rca_prompt()], None
            else:
                st = self.qetat()
                if (st["r"].get(str(st["i"])) or "").strip():   # question déjà répondue : on garde, on avance
                    self.noter("s", "Réponse conservée, question suivante.")
                    return self.avancer_question(), None
                return ["ℹ️ « ok » n'est pas une réponse : voici la question en cours.",
                        self.question_msg()], None
        if t.startswith("?") or t.endswith("?"):
            question = t.strip("? ")
            self.noter("q", question)
            rep = self.coach_question(question)
            self.noter("a", rep)
            self.save()
            return [rep] + ([self.question_msg()] if guide and self.questions_restantes() else []), None
        if guide:
            if t == "-":
                return self.commande("/sauter", "")
            return self.repondre_guide(t.lstrip("=+ ").strip() or t), None
        if t.startswith("="):                              # remplace toute la réponse de l'étape
            self.brouillon = t[1:].strip()
        else:                                              # par défaut : vos messages s'additionnent
            ajout = t[1:].strip() if t.startswith("+") else t
            self.brouillon = (self.brouillon + "\n\n" + ajout).strip() if self.brouillon else ajout
        self.noter("u", t)
        self.save()                                        # le message est conservé même si le LLM échoue
        retour = self.coach_feedback()
        self.noter("c", retour)
        self.save()
        return [msg(retour, BTN_RETOUR_COACH)], None

# ----------------------------------------------------------------------------
# Splash Screen (mode terminal uniquement)
# ----------------------------------------------------------------------------
def afficher_splash(duree_ms=SPLASH_MS):
    """Écran d'accueil. Ignoré sans tkinter ni écran (bot Telegram, SSH, Termux) : jamais bloquant."""
    if tk is None or not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return
    try:
        root = tk.Tk()
        root.withdraw()
        splash = tk.Toplevel(root)
        splash.title("Problem Solving…")
        splash.resizable(False, False)
        splash.attributes("-topmost", True)

        try:
            img = Image.open(ICON_DIR / SPLASH_IMAGE)
            img.thumbnail((220, 220))                      # garde les proportions
            photo = ImageTk.PhotoImage(img)
            tk.Label(splash, image=photo).pack(pady=(15, 5))
            splash.image_ref = photo                       # évite le ramasse-miettes
        except Exception as e:
            print(f"[SPLASH] Erreur image : {e}")
            tk.Label(splash, text="🧩 Problem Solving",
                     font=("Arial", 22, "bold")).pack(padx=30, pady=40)

        tk.Label(splash, text="Chargement de Problem Solving…",
                 font=("Arial", 11)).pack(padx=30, pady=(0, 15))

        splash.update_idletasks()                          # centre sur l'écran
        w, h = splash.winfo_reqwidth(), splash.winfo_reqheight()
        x = (splash.winfo_screenwidth() - w) // 2
        y = (splash.winfo_screenheight() - h) // 2
        splash.geometry(f"{w}x{h}+{x}+{y}")

        root.after(duree_ms, root.quit)
        root.mainloop()
        root.destroy()
    except Exception as e:                                 # ex. tk.TclError (pas d'écran réel)
        print(f"[SPLASH] ignoré : {e}")

# ----------------------------------------------------------------------------
# Mode terminal : couleurs, largeur, taille de fenêtre
# ----------------------------------------------------------------------------
# Cyan = commandes · jaune = têtes de chapitre · rouge = avertissements · vert = invite et succès
ANSI_FIN = "\033[0m"
C_CYAN, C_JAUNE, C_ROUGE, C_VERT, C_GRIS, C_GRAS = "1;36", "1;33", "1;31", "1;32", "2", "1"
RE_CMD = re.compile(r"(?<![\w/~.:])(/[a-zé0-9][\w]*)")
RE_GRAS = re.compile(r"\*\*(.+?)\*\*")
RE_LIBELLE = re.compile(r"^((?:[^\w\s/]\S*\s+)?[A-ZÉÀ][^:\n]{0,30}? :)(?=\s|$)")
TETES = ("🐟", "🌳", "📍", "🚀", "🧭", "📂", "📜", "🎉", "🔎", "💬", "❓", "🤖", "📄", "🧾", "🗂")
COULEUR_ACTIVE = True

def teinte(code, texte):
    return f"\033[{code}m{texte}{ANSI_FIN}" if COULEUR_ACTIVE else texte

def largeur_terminal():
    import shutil
    return max(60, shutil.get_terminal_size((100, 30)).columns - 1)

def agrandir_terminal(cols=110, rows=38):           
    """Demande au terminal de prendre la taille voulue (séquence xterm « resize »), en plus petit comme
    en plus grand : la fenêtre n'occupe pas tout l'écran. Ignoré sans terminal interactif ; certains
    terminaux la refusent (voir README : --cols/--rows, --no-resize)."""
    import shutil
    if not sys.stdout.isatty() or os.environ.get("TERM", "") in ("", "dumb", "linux"):
        return
    taille = shutil.get_terminal_size((0, 0))
    if taille.columns == cols and taille.lines == rows:
        return
    sys.stdout.write(f"\033[8;{rows};{cols}t")
    sys.stdout.flush()
    time.sleep(0.3)

def rendre_cli(texte):
    """Mise en forme terminal : markdown léger, retour à la ligne à la largeur réelle, couleurs."""
    import textwrap
    largeur = largeur_terminal()
    sortie = []
    for ligne in texte.splitlines():
        ligne = re.sub(r"^\s{0,3}#{1,6}\s+(.*)$", r"\1", ligne) if ligne.lstrip().startswith("#") else ligne
        ligne = ligne.replace("`", "")
        gras = bool(RE_GRAS.search(ligne))
        if gras:
            ligne = RE_GRAS.sub(r"\1", ligne)
        if not ligne.strip():
            sortie.append("")
            continue
        indent = re.match(r"^(\s*(?:[-•*]\s+|\d+[.)]\s+)?)", ligne).group(1)
        morceaux = textwrap.wrap(ligne, width=largeur, subsequent_indent=" " * len(indent),
                                 break_long_words=False, break_on_hyphens=False) or [ligne]
        tete = (ligne.startswith(TETES) or ligne.startswith(("┌─ ", "├─ "))
                or re.match(r"^──.*──$", ligne.strip()) is not None)
        alerte = ligne.startswith("⚠️")
        succes = ligne.startswith(("✅", "✔️", "💾", "🎉"))
        for k, m in enumerate(morceaux):
            if COULEUR_ACTIVE:
                base = C_JAUNE if tete else C_ROUGE if alerte else C_VERT if (succes and k == 0) else None
                if base is None and k == 0:                    # « Libellé : » en tête de ligne
                    lib = RE_LIBELLE.match(m)
                    if lib:
                        m = teinte(C_JAUNE, lib.group(1)) + m[len(lib.group(1)):]
                reprise = f"\033[{base}m" if base else ""
                m = RE_CMD.sub(lambda x: f"\033[{C_CYAN}m{x.group(1)}{ANSI_FIN}{reprise}", m)
                if base:
                    m = f"\033[{base}m{m}{ANSI_FIN}"
            sortie.append(m)
    return "\n".join(sortie)

def invite_saisie():
    """Invite « » » (codes \001/\002 : readline calcule ainsi la largeur visible)."""
    return "\001\033[1;32m\002» \001\033[0m\002" if COULEUR_ACTIVE else "» "

FICHIER_HISTORIQUE = Path(os.getenv("PS_HISTORY", str(BASE_DIR / ".problemSolving_history")))

def charger_historique_saisie():
    """Rappelle les saisies des sessions précédentes (flèches haut/bas) et les enregistre à la sortie."""
    try:
        import atexit
        import readline
    except ImportError:
        return
    readline.set_history_length(1000)
    try:
        readline.read_history_file(str(FICHIER_HISTORIQUE))
    except (OSError, UnicodeDecodeError):
        pass                                                  # première utilisation ou fichier illisible
    def sauver():
        try:
            readline.write_history_file(str(FICHIER_HISTORIQUE))
            os.chmod(FICHIER_HISTORIQUE, 0o600)               # les saisies peuvent être confidentielles
        except OSError:
            pass
    atexit.register(sauver)

def afficher(texte):
    """Affiche un texte ; s'il dépasse la hauteur de la fenêtre, le présente page par page
    (Entrée = suite, q = ignorer la suite) au lieu de le laisser défiler hors d'écran."""
    import shutil
    lignes = texte.split("\n")
    haut = shutil.get_terminal_size((100, 30)).lines - 3
    if not sys.stdin.isatty() or not sys.stdout.isatty() or len(lignes) <= haut:
        print(texte)
        return
    i = 0
    while i < len(lignes):
        print("\n".join(lignes[i:i + haut]))
        i += haut
        if i < len(lignes):
            try:
                rep = input(teinte(C_GRIS, f"── suite : Entrée · q pour arrêter ({len(lignes) - i} lignes) ──"))
            except (EOFError, KeyboardInterrupt):
                print()
                return
            sys.stdout.write("\033[1A\033[2K" if COULEUR_ACTIVE else "")
            if rep.strip().lower() == "q":
                return

def boucle_cli():
    charger_historique_saisie()
    s = Session("cli")
    afficher(rendre_cli(aide_tableau(court=True)))
    moteur = s.backend
    modele = s.modele("texte")
    ligne_moteur = f"🤖 Moteur : {moteur} · {modele} - mode {s.mode}"
    print("\n" + teinte(C_JAUNE, ligne_moteur))
    if moteur == "ollama":
        print(rendre_cli("⚠️ Moteur local (Ollama) : plus lent. /modele b groq pour repasser sur Groq."))
    elif not CFG["GROQ_API_KEY"]:
        print(rendre_cli("⚠️ Clé Groq introuvable (GROQ_API_KEY ou ~/Projects/Groq_agent/.groq_config)."))
    if s.methode:
        print(rendre_cli(f"📂 Projet en cours rechargé : « {s.titre} » — /etat, /historique ou /projets\n"))
        for m in s.reprise_msgs():                      # on repose la question en attente
            afficher(rendre_cli(texte_de(m)))
            print()
    else:
        print(rendre_cli("Aucun projet en cours — /8d, /a3, /pdca, /k6pts ou /projets\n"))
    while True:
        try:
            texte = input(invite_saisie())
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if texte.strip().lower() in ("/" + q for q in QUITTER):
            if s.methode:
                s.save()
                print(rendre_cli(f"💾 Projet « {s.titre} » enregistré — /projets pour le ré-ouvrir."))
            print(rendre_cli("À bientôt."))
            break
        attente = bool(texte.strip()) and not texte.lstrip().startswith("/") and s.methode
        if attente:                                           # appel IA possible : on le signale
            sys.stdout.write(teinte(C_GRIS, "… le coach réfléchit"))
            sys.stdout.flush()
        debut = time.time()
        msgs, fichier = s.handle(texte, cli=True)
        duree = time.time() - debut
        if attente:
            sys.stdout.write("\r\033[2K" if COULEUR_ACTIVE else "\r")
        for m in msgs:
            print()
            afficher(rendre_cli(texte_de(m)))
        if duree >= 1.0 and s.dernier_modele:
            print(teinte(C_GRIS, f"   ⏱ {duree:.1f} s · {s.backend} · {s.dernier_modele}"))
        print()
        s.dernier_modele = ""
        if fichier:
            print(f"(fichier : {fichier})")

def main():
    global COULEUR_ACTIVE
    ap = argparse.ArgumentParser(description="Problem Solving")
    ap.add_argument("--no-splash", action="store_true", help="n'affiche pas l'écran d'accueil")
    ap.add_argument("--no-color", action="store_true", help="désactive les couleurs")
    ap.add_argument("--no-resize", action="store_true", help="ne change pas la taille de la fenêtre")
    ap.add_argument("--cols", type=int, default=int(os.getenv("PS_COLS", "110")),       # largeur
                    help="largeur souhaitée de la fenêtre (défaut 110)")
    ap.add_argument("--rows", type=int, default=int(os.getenv("PS_ROWS", "38")),        # hauteur
                    help="hauteur souhaitée de la fenêtre (défaut 38)")
    args = ap.parse_args()
    COULEUR_ACTIVE = sys.stdout.isatty() and not args.no_color and "NO_COLOR" not in os.environ
    if not args.no_resize:
        agrandir_terminal(args.cols, args.rows)
    if not args.no_splash:
        afficher_splash()
    boucle_cli()

if __name__ == "__main__":
    main()
