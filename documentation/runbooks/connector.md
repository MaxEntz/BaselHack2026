# Runbook : connecter Claude au gateway MCP

Spike [#3](https://github.com/MaxEntz/BaselHack2026/issues/3). Objectif : dans une conversation Claude, appeler l'outil `ping` de notre serveur.

Claude.ai appelle les connecteurs **depuis le cloud d'Anthropic** (`160.79.104.0/21`), pas depuis ton navigateur. `localhost` ne suffit donc pas : il faut une URL HTTPS publique. Cela vaut aussi pour Claude Desktop. Seul Claude Code peut viser `localhost` directement.

**URL du connecteur :** `https://191-96-88-152.sslip.io/mcp`. Elle est servie par notre LXC (`191.96.88.152`), sans tunnel. Passe directement à l'étape 5.

## État de validation

| Maillon | État | Comment |
|---|---|---|
| `ping` en HTTPS sur l'URL publique | ✅ validé | client du SDK depuis l'extérieur, certificat Let's Encrypt valide |
| URL stable après redémarrage du LXC | ✅ validé | même URL, `ping` OK après reboot |
| `allowed_hosts`, `Origin`, bearer token, redirection `https` | ✅ validé en local | vrai serveur, en-têtes simulés avec curl |
| Claude Code (plan B), avec et sans token | ✅ validé | appel réel de `ping` en local |
| Claude.ai | ✅ validé | `ping` appelé depuis une conversation, capture ci-dessous |
| Appels de Claude.ai acceptés par le serveur | ✅ validé | venus de `160.79.106.x`, en `200`, aucun `Host` ni `Origin` rejeté dans les logs |
| MCP Inspector | ⏳ pas encore lancé | étape 4 |
| Tunnel ngrok (étapes 1 à 3) | ⏳ non testé | alternative si le LXC n'est plus disponible |

![Claude.ai appelle ping et affiche pong](img/connector-ping.png)

## Déployer une modification sur le LXC

```bash
cd code
make deploy
```

Il faut la clé `~/.ssh/baselhack2026_lxc`. Si elle n'est pas sur la machine, demande-la à Del, qui peut l'avoir. Un coéquipier peut aussi faire ajouter sa clé publique au LXC, puis lancer `make deploy DEPLOY_SSH="ssh -i <sa-clé> -o IdentitiesOnly=yes"`. Une clé privée ne se commite jamais.

```bash
ssh -i ~/.ssh/baselhack2026_lxc -o IdentitiesOnly=yes root@191.96.88.152
```

Sur le serveur, Caddy gère le HTTPS et renvoie vers `127.0.0.1:8001`. Le service `merchant-gateway` tourne sous un utilisateur sans droits. Sa configuration est dans `/opt/merchant-agents/code/.env`, et ses logs avec `journalctl -u merchant-gateway`. `make deploy` ne touche pas à ce `.env` : sur un nouveau serveur, il faut le créer avec `DATABASE_URL`, sans laquelle le service ne démarre pas, et `GATEWAY_PUBLIC_URL=https://191-96-88-152.sslip.io`, sans laquelle toute requête publique répond `421`.

⚠️ **Le LXC est partagé, il ne sert pas qu'au gateway.** Demande avant de le redémarrer, d'arrêter un service ou de toucher à quoi que ce soit hors de `/opt/merchant-agents`. Si `code/deploy/Caddyfile` a changé, `make deploy` le valide, écrase `/etc/caddy/Caddyfile` et redémarre Caddy : tout autre site doit aussi y être déclaré. Le pare-feu (`/etc/nftables.conf`) n'ouvre que les ports 22, 80 et 443 en entrée, et refuse les nouvelles connexions sortantes vers les réseaux privés.

## Prérequis

- Un compte Claude qui peut ajouter un connecteur custom : Free (1 seul), Pro, Max, Ultra, Team ou Enterprise. En Team, seul un Owner ou Primary Owner peut l'ajouter. En Enterprise, aussi un rôle custom avec *Manage access to Libraries*.
- Uniquement pour l'alternative ngrok : `uv`, `ngrok` et un compte ngrok (gratuit).

## Pas à pas

Les étapes 1 à 3 ne servent que sans le LXC, pour exposer un serveur local par tunnel. Avec le LXC, remplace `<nom>.ngrok-free.app` par `191-96-88-152.sslip.io` dans les étapes 4 et 5.

### 1. Configurer ngrok (une seule fois)

```bash
ngrok config add-authtoken <token>   # https://dashboard.ngrok.com/get-started/your-authtoken
```

Récupère ton domaine statique gratuit sur https://dashboard.ngrok.com/domains. C'est lui qui garde la même URL après un redémarrage : le connecteur n'a pas à être recréé.

### 2. Configurer le projet

```bash
cd code
uv sync
test -f .env || cp .env.example .env
```

Dans `.env`, sans `/mcp` à la fin :

```bash
GATEWAY_PUBLIC_URL=https://<nom>.ngrok-free.app
GATEWAY_TOKEN=
```

### 3. Lancer le serveur et le tunnel

```bash
make gateway   # terminal 1
make tunnel    # terminal 2
```

Vérifie depuis un autre réseau si possible (partage 4G/5G) :

```bash
curl -i https://<nom>.ngrok-free.app/mcp
```

Un `400` montre que la requête atteint bien le serveur (`401` si `GATEWAY_TOKEN` est rempli). Un `421` veut dire que `GATEWAY_PUBLIC_URL` est faux ou que le serveur n'a pas été relancé.

### 4. Tester avec MCP Inspector

```bash
npx -y @modelcontextprotocol/inspector --cli https://<nom>.ngrok-free.app/mcp --transport http --method tools/list
npx -y @modelcontextprotocol/inspector --cli https://<nom>.ngrok-free.app/mcp --transport http --method tools/call --tool-name ping
```

Sans Node, le client du SDK fait le même test :

```bash
uv run python -c '
import asyncio
from mcp import Client

async def main():
    async with Client("https://<nom>.ngrok-free.app/mcp") as client:
        print(await client.call_tool("ping", {}))

asyncio.run(main())'
```

### 5. Ajouter le connecteur dans Claude.ai

**Pro, Max, Free, Ultra**

1. Va dans **Customize > Connectors** (https://claude.ai/customize/connectors).
2. Clique sur **+ Add**, puis **Add custom connector**.
3. Renseigne le nom `merchant-agents` et l'URL `https://<nom>.ngrok-free.app/mcp`. Le chemin `/mcp` est obligatoire.
4. Pour **Authentication**, choisis **No sign in**.
5. Clique sur **Add**.

**Team, Enterprise**

1. Un Owner va dans **Organization settings > Connectors** (https://claude.ai/admin-settings/connectors).
2. Il clique sur **Add**, survole **Custom**, puis choisit **Web**.
3. Chaque membre clique ensuite sur **Connect** dans **Customize > Connectors**.

La présentation de la boîte de dialogue varie selon les organisations.

**Dans une conversation**

1. Ouvre **+ > Connectors** et active `merchant-agents`.
2. Demande : « Appelle l'outil ping du connecteur merchant-agents ».
3. Approuve l'appel si Claude le demande. La réponse doit mentionner `pong` et l'heure du serveur.

## Plan B : Claude Code

Pas besoin de tunnel. Une fois `make gateway` lancé :

```bash
claude mcp add --transport http merchant-agents http://localhost:8001/mcp
# avec GATEWAY_TOKEN :
claude mcp add --transport http merchant-agents http://localhost:8001/mcp --header "Authorization: Bearer <token>"
```

Lance `/mcp` dans une session pour vérifier la connexion, puis demande d'appeler `ping`.

Claude Desktop n'est **pas** un plan B indépendant : son connecteur custom passe lui aussi par le cloud d'Anthropic, donc si le tunnel tombe, il tombe aussi.

## Protéger l'URL publique

`GATEWAY_TOKEN` exige `Authorization: Bearer <token>` sur toutes les requêtes. Vide, le serveur est ouvert à quiconque connaît l'URL.

⚠️ Côté Claude.ai, l'authentification par en-tête est **en bêta, réservée à certaines organisations**. Sans section **Request headers** dans la boîte de dialogue, il n'y a pas accès : si `GATEWAY_TOKEN` est rempli, le connecteur ne pourra pas se connecter.

Si la section est présente :

1. Laisse **Authentication** sur **No sign in**.
2. Dans **Request headers**, choisis `authorization`.
3. Saisis la valeur complète `Bearer <token>`. Claude n'ajoute aucun préfixe.

On ne peut pas changer l'authentification après coup : il faut supprimer le connecteur et le recréer. Ne pas passer le token dans l'URL (`?token=`), la doc le déconseille.

Sans accès à la bêta, deux options :

- Couper le tunnel en dehors des tests et de la démo. C'est la solution simple.
- Filtrer `160.79.104.0/21` avec une traffic policy ngrok (`--traffic-policy-file`). Non testé. Inspector et `curl` depuis ta machine seraient alors bloqués.

⚠️ Sur le LXC, il n'y a pas de tunnel à couper : sans `GATEWAY_TOKEN`, l'URL reste ouverte en permanence. Sans risque tant que seul `ping` existe, mais à régler avant d'ajouter un outil qui écrit (`create_reservation`, `cancel_reservation`). Sans accès à la bêta, le filtre équivalent est `remote_ip 160.79.104.0/21` dans le Caddyfile. Non testé.

## Erreurs possibles

| Symptôme | Cause | Solution |
|---|---|---|
| `421 Invalid Host header` | Domaine du tunnel absent de `allowed_hosts` | Corriger `GATEWAY_PUBLIC_URL`, relancer `make gateway` |
| `403 Invalid Origin header` | `Origin` envoyé hors de `ALLOWED_ORIGINS` | L'ajouter dans `gateway/server.py`. Le nom refusé est dans les logs du serveur |
| `401 Unauthorized` | Token absent, faux, ou sans préfixe `Bearer ` | Corriger l'en-tête, ou vider `GATEWAY_TOKEN` |
| `make tunnel` : `GATEWAY_PUBLIC_URL is empty` | `.env` absent ou incomplet | Étape 2 |
| `make gateway` : `database_url Field required` | `.env` absent | Étape 2 |
| `307` vers `/mcp` | URL saisie avec un `/` final | Utiliser exactement `/mcp` |
| `RuntimeError: Task group is not initialized` | App MCP montée dans une autre app sans lifespan | Entrer `mcp.session_manager.run()` dans le lifespan de l'app hôte. Sans objet aujourd'hui : l'app n'est pas montée |
| « Couldn't reach » ou connexion bloquée | Wi-Fi du hackathon, ou blocage réseau | Retester en partage 4G/5G |

## Décisions

- **SDK v2 conservé** (`mcp` 2.3.0), pas de repli sur `mcp<2`. Validé depuis Claude.ai et Claude Code.
- **LXC public plutôt que tunnel** : URL fixe, et la démo ne dépend ni d'un Mac ni du Wi-Fi du hackathon. En contrepartie, une modification doit être déployée pour que Claude.ai la voie. ngrok reste l'alternative documentée.
- **`sslip.io`** : un nom public gratuit pour obtenir un certificat, sans acheter de domaine. C'est un service tiers : un vrai domaine serait plus sûr pour la démo.
- **Écoute sur `127.0.0.1`** : seul Caddy joint le serveur, qui n'est pas exposé directement. L'issue #11 prévoit `0.0.0.0`, à ne reprendre que si un conteneur l'exige.
- **Un seul worker uvicorn** : les sessions des clients sur l'ancien protocole (avant 2026-07-28) vivent en mémoire d'un process.

## Références

- [Custom connectors (support)](https://support.claude.com/en/articles/11175166-get-started-with-custom-connectors-using-remote-mcp)
- [Ajouter un connecteur custom](https://claude.com/docs/connectors/custom/add-unlisted)
- [Authentification des connecteurs](https://claude.com/docs/connectors/building/authentication.md)
- [Dépannage des connecteurs](https://claude.com/docs/connectors/building/troubleshooting.md)
- [SDK MCP v2 : Deploy & scale](https://py.sdk.modelcontextprotocol.io/run/deploy/)
