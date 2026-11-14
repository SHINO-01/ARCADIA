# ARCADIA developer entry points. Web stack = Docker; robotics = native ROS 2 + Webots.
SHELL := /bin/bash
ROS_DISTRO ?= lyrical
WS := robotics/ros2_ws
API_URL ?= http://localhost:8080/api
export WEBOTS_HOME ?= $(HOME)/opt/webots

.PHONY: help up down logs ps web-build backend-dev backend-test frontend-dev frontend-build \
        robotics-build sim sim-headless seed-demo clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

up: ## Start the containerised web stack (Caddy, API, Celery, React, Postgres, Redis)
	docker compose up -d --build
	@echo "Dashboard: http://localhost:$${HTTP_PORT:-8080}   API docs: http://localhost:$${HTTP_PORT:-8080}/api/docs"

down: ## Stop the web stack
	docker compose down

logs: ## Tail web-stack logs
	docker compose logs -f --tail=100 api worker beat caddy

ps: ## Show container status
	docker compose ps

backend-dev: ## Run the API natively against the compose Postgres/Redis (hot reload)
	cd backend && ARCADIA_ROSBRIDGE_URL=ws://localhost:9090 .venv/bin/alembic upgrade head && \
	ARCADIA_ROSBRIDGE_URL=ws://localhost:9090 .venv/bin/uvicorn app.main:app --reload --port 8000

backend-test: ## Run backend unit tests
	cd backend && .venv/bin/python -m pytest -q

frontend-dev: ## Vite dev server with API proxy to :8000
	cd frontend && npm run dev

frontend-build: ## Production build of the dashboard
	cd frontend && npm run build

robotics-build: ## colcon build of the ROS 2 workspace (native)
	source /opt/ros/$(ROS_DISTRO)/setup.bash && cd $(WS) && \
	colcon build --symlink-install --cmake-args -DCMAKE_BUILD_TYPE=Release

sim: ## Launch Webots + fleet controllers + rosbridge + diagnostics (native)
	source /opt/ros/$(ROS_DISTRO)/setup.bash && source $(WS)/install/setup.bash && \
	ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST ros2 launch arcadia_bringup arcadia.launch.py api_url:=$(API_URL)

sim-headless: ## Launch the ROS 2 fleet without the Webots GUI (minimised, no rendering)
	source /opt/ros/$(ROS_DISTRO)/setup.bash && source $(WS)/install/setup.bash && \
	ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST ros2 launch arcadia_bringup arcadia.launch.py gui:=false api_url:=$(API_URL)

seed-demo: ## Create, plan and activate a demo farm through the API
	python3 scripts/seed_demo.py

clean: ## Remove build artefacts
	rm -rf $(WS)/build $(WS)/install $(WS)/log frontend/dist
