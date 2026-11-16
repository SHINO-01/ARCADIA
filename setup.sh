#!/usr/bin/env bash
# ARCADIA - one-line setup.
#
#   git clone https://github.com/SHINO-01/ARCADIA.git && ARCADIA/setup.sh
#   curl -fsSL https://raw.githubusercontent.com/SHINO-01/ARCADIA/main/setup.sh | bash     (public repo)
#   curl ... | bash -s -- --tunnel --demo                                                    (with options)
#
# Default: the web stack in Docker (dashboard, API, Celery, TimescaleDB, Redis, Caddy) running the
# agronomic world model with the headless digital twin; it switches to Webots automatically when
# the native simulator runs on the same machine.
#
# Options
#   --full              also install native ROS 2 + Webots and build the robotics workspace
#                       (Ubuntu x86-64; asks for sudo), then start it with `make sim`
#   --cloud             server mode: headless twin only, databases bound to localhost
#   --tunnel            publish the dashboard through a free Cloudflare quick tunnel (random https URL)
#   --tunnel-token TOK  publish through your own named Cloudflare tunnel (your domain)
#   --demo              create, analyse, plan and activate a demo farm
#   --port N            dashboard port (default 8080, or the next free one)
#   --dir DIR           clone target when run outside the repository (default ./ARCADIA)
#   --branch B          git branch to clone (default main)
#   --project NAME      Docker Compose project name (default arcadia) - for a second install on one machine
#   -y, --yes           do not ask before installing Docker
set -euo pipefail

REPO_URL="${ARCADIA_REPO:-https://github.com/SHINO-01/ARCADIA.git}"
BRANCH=main; DIR=ARCADIA; PROJECT=""; PORT=""; FULL=0; CLOUD=0; TUNNEL=0; TOKEN=""; DEMO=0; YES=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --full) FULL=1 ;;
    --cloud) CLOUD=1 ;;
    --tunnel) TUNNEL=1 ;;
    --tunnel-token) TOKEN="${2:?--tunnel-token needs a token}"; shift ;;
    --demo) DEMO=1 ;;
    --port) PORT="${2:?--port needs a number}"; shift ;;
    --dir) DIR="${2:?--dir needs a path}"; shift ;;
    --branch) BRANCH="${2:?--branch needs a name}"; shift ;;
    --project) PROJECT="${2:?--project needs a name}"; shift ;;
    -y|--yes) YES=1 ;;
    -h|--help) sed -n '2,27p' "${BASH_SOURCE[0]:-$0}" 2>/dev/null | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1 (try --help)" >&2; exit 2 ;;
  esac
  shift
done

if [[ -t 1 ]]; then B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; D=$'\e[2m'; N=$'\e[0m'; else B=; G=; Y=; R=; D=; N=; fi
step() { echo "${B}${G}==>${N}${B} $*${N}"; }
note() { echo "    ${D}$*${N}"; }
warn() { echo "${Y}!!${N} $*" >&2; }
die()  { echo "${R}xx${N} $*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
port_free() { ! (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }
next_free() { local p=$1; while ! port_free "$p"; do p=$((p + 1)); done; echo "$p"; }

OS="$(uname -s)"; ARCH="$(uname -m)"
step "ARCADIA setup on ${OS}/${ARCH}"

# ---------------------------------------------------------------------------- prerequisites
for t in git curl; do have "$t" || die "$t is required (sudo apt install $t)"; done
if ! have docker; then
  [[ "$OS" == "Linux" ]] || die "Install Docker Desktop first: https://docs.docker.com/get-docker/"
  if [[ $YES -eq 0 && -t 0 ]]; then
    read -r -p "Docker is not installed. Install it now with get.docker.com (needs sudo)? [Y/n] " a
    [[ "${a:-y}" =~ ^[Yy]$ ]] || die "Docker is required"
  fi
  step "Installing Docker Engine"
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER" || true
fi
DOCKER=(docker)
if ! docker info >/dev/null 2>&1; then
  if sudo -n true 2>/dev/null || [[ -t 0 ]]; then DOCKER=(sudo docker); note "using sudo for docker (log out and in once to drop it)"; else die "cannot talk to the Docker daemon"; fi
  "${DOCKER[@]}" info >/dev/null 2>&1 || die "the Docker daemon is not running (sudo systemctl start docker)"
fi
"${DOCKER[@]}" compose version >/dev/null 2>&1 || die "Docker Compose v2 is required (docker compose)"

# ---------------------------------------------------------------------------- repository
here="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || pwd)"
if [[ -f "$here/docker-compose.yml" && -d "$here/backend" ]]; then ROOT="$here"
elif [[ -f ./docker-compose.yml && -d ./backend ]]; then ROOT="$(pwd)"
else
  step "Cloning ${REPO_URL} (${BRANCH}) into ${DIR}"
  if [[ -d "$DIR/.git" ]]; then git -C "$DIR" pull --ff-only
  elif have gh && gh auth status >/dev/null 2>&1 && [[ "$REPO_URL" == *github.com/* ]]; then
    gh repo clone "${REPO_URL#*github.com/}" "$DIR" -- -b "$BRANCH"
  else git clone -b "$BRANCH" "$REPO_URL" "$DIR"; fi
  ROOT="$(cd "$DIR" && pwd)"
fi
cd "$ROOT"
note "project: $ROOT"
[[ -n "$PROJECT" ]] && export COMPOSE_PROJECT_NAME="$PROJECT"
# Never take over another ARCADIA install that runs under the same compose project name
proj="${COMPOSE_PROJECT_NAME:-$(sed -n 's/^name:[[:space:]]*//p' docker-compose.yml | head -1)}"
other="$("${DOCKER[@]}" ps --filter "label=com.docker.compose.project=${proj}" --format '{{.Label "com.docker.compose.project.working_dir"}}' | sort -u | grep -vx "$ROOT" || true)"
[[ -z "$other" ]] || die "an ARCADIA stack from ${other} is already running as project '${proj}'. Stop it (docker compose down there) or install side by side with --project NAME"

# ---------------------------------------------------------------------------- configuration
setenv() { # key value: set or replace a line in .env
  if grep -q "^$1=" .env; then sed -i.bak "s|^$1=.*|$1=$2|" .env && rm -f .env.bak; else echo "$1=$2" >> .env; fi
}
if [[ ! -f .env ]]; then
  step "Writing .env"
  cp .env.example .env
  setenv POSTGRES_PASSWORD "$(head -c 18 /dev/urandom | od -An -tx1 | tr -d ' \n')"
fi
# shellcheck disable=SC1091
set -a; source ./.env; set +a
running=$("${DOCKER[@]}" compose ps -q caddy 2>/dev/null || true)
if [[ -n "$PORT" ]]; then setenv HTTP_PORT "$PORT"
elif [[ -z "$running" ]] && ! port_free "${HTTP_PORT:-8080}"; then setenv HTTP_PORT "$(next_free "${HTTP_PORT:-8080}")"; fi
if [[ -z "$running" ]]; then
  port_free "${HTTPS_PORT:-8443}" || setenv HTTPS_PORT "$(next_free "${HTTPS_PORT:-8443}")"
  port_free "${POSTGRES_PORT:-55432}" || setenv POSTGRES_PORT "$(next_free "${POSTGRES_PORT:-55432}")"
  port_free "${REDIS_PORT:-6379}" || setenv REDIS_PORT "$(next_free 16379)"
fi
[[ $CLOUD -eq 1 ]] && setenv ARCADIA_BRIDGE_MODE headless
[[ -n "$TOKEN" ]] && setenv CLOUDFLARE_TUNNEL_TOKEN "$TOKEN"
set -a; source ./.env; set +a
PROFILES=()
[[ $TUNNEL -eq 1 ]] && PROFILES+=(--profile tunnel)
[[ -n "$TOKEN" ]] && PROFILES+=(--profile tunnel-named)

# ---------------------------------------------------------------------------- start
step "Building and starting the web stack (first run takes a few minutes)"
"${DOCKER[@]}" compose "${PROFILES[@]}" up -d --build
URL="http://localhost:${HTTP_PORT:-8080}"
printf "    waiting for the API"
for _ in $(seq 1 120); do
  if curl -fs "$URL/api/health" >/dev/null 2>&1; then echo " ${G}ok${N}"; break; fi
  printf "."; sleep 3
done
curl -fs "$URL/api/health" >/dev/null 2>&1 || { echo; die "the API did not come up: ${DOCKER[*]} compose logs api"; }

PUBLIC=""
if [[ $TUNNEL -eq 1 ]]; then
  printf "    opening the Cloudflare quick tunnel"
  for _ in $(seq 1 30); do
    PUBLIC="$("${DOCKER[@]}" compose logs tunnel 2>/dev/null | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1 || true)"
    [[ -n "$PUBLIC" ]] && break; printf "."; sleep 2
  done
  echo
  [[ -n "$PUBLIC" ]] || warn "tunnel URL not found yet: ${DOCKER[*]} compose logs tunnel"
fi

# ---------------------------------------------------------------------------- robotics (native)
if [[ $FULL -eq 1 ]]; then
  [[ "$OS" == "Linux" && "$ARCH" == "x86_64" ]] || die "--full needs Linux x86-64 (Webots has no official ARM build)"
  have apt-get || die "--full needs an apt-based distribution (Ubuntu)"
  step "Installing ROS 2 (sudo)"
  sudo bash robotics/scripts/install_ros2_apt.sh
  step "Installing Webots (user space, ~/opt/webots)"
  [[ -x "${WEBOTS_HOME:-$HOME/opt/webots}/webots" ]] || bash robotics/scripts/install_webots_tarball.sh
  step "Building the ROS 2 workspace"
  export WEBOTS_HOME="${WEBOTS_HOME:-$HOME/opt/webots}"
  make robotics-build
fi

if [[ $DEMO -eq 1 ]]; then
  step "Creating the demo farm (site analysis, adaptive layout, crop choice, activation)"
  ARCADIA_API="$URL/api" python3 scripts/seed_demo.py || warn "demo farm setup failed; create one in the dashboard instead"
fi

echo
step "ARCADIA is running"
echo "    dashboard   ${B}${URL}${N}"
[[ -n "$PUBLIC" ]] && echo "    public      ${B}${PUBLIC}${N}   (Cloudflare quick tunnel, changes on restart)"
[[ -n "$TOKEN" ]] && echo "    public      your Cloudflare tunnel hostname"
echo "    API docs    ${URL}/api/docs"
if [[ $FULL -eq 1 ]]; then echo "    simulator   cd $ROOT && make sim      (Webots + ROS 2; the dashboard switches to it)"
elif [[ $CLOUD -eq 0 ]]; then echo "    simulator   optional: $0 --full, then make sim"; fi
echo "    stop        cd $ROOT && ${DOCKER[*]} compose down"
