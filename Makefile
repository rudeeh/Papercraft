.PHONY: up down build logs test

up:
	docker-compose up -d

down:
	docker-compose down

build:
	docker-compose build

logs:
	docker-compose logs -f

test:
	docker-compose run api pytest
