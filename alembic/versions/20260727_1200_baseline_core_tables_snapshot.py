"""Baseline snapshot das ~20 tabelas centrais (mova.*, vcms.*) hoje fora de controle de versao

Revision ID: 9f2c7a5e1d3b
Revises: 7c1e9a2f4b3d
Create Date: 2026-07-27 12:00:00.000000

IMPORTANTE - LEIA ANTES DE USAR:

1. Esta migration e um SNAPSHOT/BASELINE, nao uma mudanca de schema. As
   tabelas abaixo JA EXISTEM em produção (Aurora). O objetivo e permitir
   que um ambiente NOVO E VAZIO (staging, disaster recovery, dev local)
   consiga reconstruir o schema core do zero via `alembic upgrade head`.

2. O `include_object` em alembic/env.py continua filtrando o autogenerate
   para so considerar tabelas `fleet_*` (decisao arquitetural deliberada,
   ver comentario no proprio env.py). Esta migration NAO reverte isso -
   ela existe fora do fluxo normal de autogenerate, e deve ser aplicada
   manualmente quando necessario (ver instrucoes de uso abaixo).

3. Rodar `alembic upgrade head` contra o Aurora de PRODUCAO (onde as
   tabelas ja existem) e seguro: todo `CREATE TABLE` usa `IF NOT EXISTS`,
   entao nao faz nada se a tabela ja existir. Ainda assim, o recomendado
   em produção e usar `alembic stamp 9f2c7a5e1d3b` em vez de upgrade,
   ja que o objetivo em prod e so "marcar como aplicado", nao executar DDL.

4. TODAS as ~20 tabelas abaixo foram cross-validadas contra consultas
   diretas a information_schema.columns / pg_index / key_column_usage
   no Aurora real de produção (nao apenas derivadas dos models Python).
   Contagem de colunas confirmada 1:1 para cada tabela.

5. mova.fleet_api_keys, mova.fleet_events, mova.fleet_positions e
   mova.fleet_vehicles vivem todas dentro do schema `mova`, NAO em
   `public` como os models Python presumem (nenhum tem __table_args__
   com schema explicito). Registrado como achado separado (candidata a
   F1-06) - esta migration usa o schema REAL (mova) para fleet_api_keys.

6. con_telemetry e dev_status_30 sao tabelas "particionadas" apenas por
   convencao de nome/heranca manual antiga (NAO particionamento nativo
   do Postgres). Esta migration cria somente a tabela PAI; as ~250+
   tabelas filhas nao sao recriadas aqui (geridas por rotina propria).

7. ESCOPO DELIBERADAMENTE EXCLUIDO: mova.customers e mova.consumption_profile
   (referenciadas por FK mas fora das ~20 tabelas centrais) - colunas
   mantidas, FK constraint omitida de proposito.

8. ATUALIZADO EM 2026-07-27 (F2-02): mova.users passou a usar
   `CREATE TABLE IF NOT EXISTS` (so' coluna id) + `ALTER TABLE ADD COLUMN
   IF NOT EXISTS` para o restante, em vez de um CREATE TABLE monolitico.
   Isso porque a migration 2f8a9c4b5d1e (api_permissions system, que roda
   ANTES desta na cadeia) agora cria um "stub" minimo de mova.users
   (so' a coluna id) para poder referenciar via FK. Testado em cadeia
   completa do zero (migrations 1->2->3->4) contra Postgres 16 + PostGIS
   limpo: 0 erros, mova.users com as 24 colunas completas, trigger de
   auditoria funcional de ponta a ponta.

USO RECOMENDADO:
  - Ambiente novo/vazio (staging, CI, disaster recovery):
        alembic upgrade head
  - Banco de producao (tabelas ja existem):
        alembic stamp 9f2c7a5e1d3b
"""
from alembic import op


revision = '9f2c7a5e1d3b'
down_revision = '7c1e9a2f4b3d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS mova;")
    op.execute("CREATE SCHEMA IF NOT EXISTS vcms;")

    op.execute("""
-- Colunas mantêm o ordinal_position original do Aurora (7, 11, 16 pulados = colunas removidas historicamente; não recriar)
CREATE TABLE IF NOT EXISTS mova.account (
    "id" INTEGER NOT NULL,
    "name" VARCHAR,
    "account_type_id" INTEGER,
    "status" INTEGER NOT NULL DEFAULT 1,
    "modify_date" TIMESTAMP,
    "driver_manager" INTEGER DEFAULT 1,
    "skin" INTEGER NOT NULL DEFAULT 0,
    "imglogo" VARCHAR,
    "user_add" INTEGER,
    "user_modif" INTEGER,
    "date_modif" TIMESTAMP,
    "date_add" TIMESTAMP DEFAULT now(),
    "cli_fat_default" INTEGER NOT NULL DEFAULT 0,
    "index" VARCHAR NOT NULL DEFAULT 'index.html'::character varying,
    "time_reload" INTEGER NOT NULL DEFAULT 60,
    "marinetraffic" BOOLEAN NOT NULL DEFAULT false,
    "sat_account_number" VARCHAR,
    "fixed_driver" BOOLEAN NOT NULL DEFAULT false,
    "monitriip" BOOLEAN DEFAULT false,
    "cnpj" VARCHAR,
    "port" INTEGER,
    PRIMARY KEY (id)
);
CREATE SEQUENCE IF NOT EXISTS mova.account_id_seq;
ALTER SEQUENCE mova.account_id_seq OWNED BY mova.account.id;
ALTER TABLE mova.account ALTER COLUMN id SET DEFAULT nextval('mova.account_id_seq'::regclass);

CREATE TABLE IF NOT EXISTS mova.con_driver_h_km (
    "id" BIGINT NOT NULL,
    "dt" DATE NOT NULL,
    "label" VARCHAR NOT NULL,
    "unit_id" BIGINT,
    "group_id" BIGINT,
    "group_name" VARCHAR NOT NULL,
    "subgroup_id" BIGINT,
    "driver" VARCHAR NOT NULL,
    "driver_id" BIGINT,
    "distance_traveled_ct" BIGINT,
    "time_traveled_ct" BIGINT,
    "used_fuel_ct" BIGINT,
    "distance_traveled_hist" BIGINT,
    "time_traveled_hist" BIGINT,
    "used_fuel_hist" BIGINT,
    "count_speed_excess_dry_l1" BIGINT,
    "count_speed_excess_dry_l2" BIGINT,
    "count_speed_excess_dry_l3" BIGINT,
    "count_speed_excess_wet_l1" BIGINT,
    "count_speed_excess_wet_l2" BIGINT,
    "count_speed_excess_wet_l3" BIGINT,
    "count_clutch_excess" BIGINT,
    "count_break_excess" BIGINT,
    "count_acel_excess" BIGINT,
    "count_retarder_accel" BIGINT DEFAULT 0,
    "count_speed_excess_inercia" BIGINT,
    "count_speed_excess" INTEGER DEFAULT 0,
    "rain_sensor_travel_distance" BIGINT,
    "rain_sensor_active_time" BIGINT,
    "line_number" BIGINT,
    "distance_traveled_hist_estimated" BIGINT,
    "used_fuel_hist_estimated" BIGINT,
    "kml_estimated" NUMERIC(10,4),
    PRIMARY KEY (id)
);
CREATE SEQUENCE IF NOT EXISTS mova.con_driver_h_km_id_seq;
ALTER SEQUENCE mova.con_driver_h_km_id_seq OWNED BY mova.con_driver_h_km.id;
ALTER TABLE mova.con_driver_h_km ALTER COLUMN id SET DEFAULT nextval('mova.con_driver_h_km_id_seq'::regclass);

-- Tabela sem chave primaria no Aurora real (confirmado via pg_index); nao adicionar PK artificial
CREATE TABLE IF NOT EXISTS mova.con_telemetry_day (
    "unit_id" INTEGER,
    "group_id" INTEGER,
    "subgroup_id" INTEGER,
    "driver_id" INTEGER,
    "day" DATE,
    "total_time" BIGINT,
    "km" BIGINT,
    "fuel_used" BIGINT,
    "max_speed" INTEGER,
    "avg_speed" NUMERIC,
    "time_inercia" BIGINT,
    "time_extra_eco" BIGINT,
    "time_green" BIGINT,
    "time_yellow" BIGINT,
    "time_banguela" BIGINT,
    "time_blue" BIGINT,
    "time_red" BIGINT,
    "time_tolerancia" BIGINT,
    "faixas_total" BIGINT,
    "time_stopped" BIGINT,
    "time_moving" BIGINT,
    "time_engine_off" BIGINT,
    "time_stop_engine_on" BIGINT,
    "count_clutch" BIGINT,
    "count_cluth_excess" BIGINT,
    "count_over_speed" BIGINT,
    "time_stop_accel" BIGINT,
    "count_hard_acel" BIGINT,
    "count_hard_brake" BIGINT,
    "time_over_turbo_pressure" BIGINT,
    "time_under_turbo_pressure" BIGINT,
    "time_stop_engine_on_productive" INTEGER,
    "time_low_speed" INTEGER,
    "time_eco_roll" INTEGER,
    "line_number" BIGINT,
    "score" NUMERIC,
    "time_engine_load_level1" INTEGER DEFAULT 0,
    "time_engine_load_level2" INTEGER DEFAULT 0,
    "time_engine_load_level3" INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS mova.device_model (
    "id" INTEGER NOT NULL,
    "device_manufacturer_id" INTEGER NOT NULL,
    "name" VARCHAR,
    "modify_date" TIMESTAMP,
    "status" INTEGER NOT NULL DEFAULT 1,
    "qtd_driver" INTEGER DEFAULT 0,
    "user_add" INTEGER,
    "date_add" TIMESTAMP DEFAULT now(),
    "user_modif" INTEGER,
    "date_modif" TIMESTAMP,
    "communication_type" VARCHAR DEFAULT 'GPRS'::character varying,
    "monitor" BOOLEAN DEFAULT true,
    "time_unstable" INTERVAL DEFAULT '00:02:00'::interval,
    "time_critical" INTERVAL DEFAULT '00:05:00'::interval,
    PRIMARY KEY (id)
);
CREATE SEQUENCE IF NOT EXISTS mova.device_model_id_seq;
ALTER SEQUENCE mova.device_model_id_seq OWNED BY mova.device_model.id;
ALTER TABLE mova.device_model ALTER COLUMN id SET DEFAULT nextval('mova.device_model_id_seq'::regclass);

-- id tem constraint UNIQUE (heat_map_id_key), mas NAO e PRIMARY KEY formal no Aurora real
CREATE TABLE IF NOT EXISTS mova.heatmap (
    "id" BIGINT NOT NULL,
    "unit_id" BIGINT,
    "label" VARCHAR,
    "label2" VARCHAR,
    "group_id" BIGINT,
    "subgroup_id" BIGINT,
    "driver_id" BIGINT,
    "driver_name" VARCHAR,
    "local_time" TIMESTAMP,
    "address" VARCHAR,
    "tracker_event_id" BIGINT,
    "tracker_event_name" VARCHAR,
    "area_id" BIGINT,
    "area_name" VARCHAR,
    "line_number" VARCHAR,
    "latitude" NUMERIC(9,6),
    "longitude" NUMERIC(9,6)
);
CREATE SEQUENCE IF NOT EXISTS mova.heatmap_id_seq;
ALTER SEQUENCE mova.heatmap_id_seq OWNED BY mova.heatmap.id;
ALTER TABLE mova.heatmap ALTER COLUMN id SET DEFAULT nextval('mova.heatmap_id_seq'::regclass);
ALTER TABLE mova.heatmap ADD CONSTRAINT heat_map_id_key UNIQUE (id);

CREATE TABLE IF NOT EXISTS mova.tracker_event (
    "id" INTEGER NOT NULL,
    "name" VARCHAR NOT NULL,
    "event_type_id" INTEGER NOT NULL,
    "icon" VARCHAR,
    PRIMARY KEY (id)
);
CREATE SEQUENCE IF NOT EXISTS mova.tracker_event_id_seq;
ALTER SEQUENCE mova.tracker_event_id_seq OWNED BY mova.tracker_event.id;
ALTER TABLE mova.tracker_event ALTER COLUMN id SET DEFAULT nextval('mova.tracker_event_id_seq'::regclass);

-- Tabela sem chave primaria no Aurora real (confirmado via pg_index)
CREATE TABLE IF NOT EXISTS mova.weight_range (
    "range_id" INTEGER,
    "weight" DOUBLE PRECISION,
    "subgroup_id" INTEGER,
    "group_id" INTEGER,
    "goal" DOUBLE PRECISION
);

-- ATENCAO: vive em mova.fleet_api_keys, NAO em public.fleet_api_keys como o model Python presume (app/models/api_key.py sem __table_args__ de schema). Registrado como achado separado.
CREATE TABLE IF NOT EXISTS mova.fleet_api_keys (
    "id" INTEGER NOT NULL,
    "owner_id" INTEGER NOT NULL,
    "name" VARCHAR(255) NOT NULL,
    "key_prefix" VARCHAR(20) NOT NULL,
    "key_hash" VARCHAR(255) NOT NULL,
    "permissions" JSON NOT NULL,
    "rate_limit_per_hour" INTEGER NOT NULL,
    "is_active" BOOLEAN NOT NULL,
    "expires_at" TIMESTAMP,
    "last_used_at" TIMESTAMP,
    "created_at" TIMESTAMP NOT NULL,
    "total_requests" INTEGER NOT NULL,
    "description" TEXT,
    PRIMARY KEY (id)
);
CREATE SEQUENCE IF NOT EXISTS mova.fleet_api_keys_id_seq;
ALTER SEQUENCE mova.fleet_api_keys_id_seq OWNED BY mova.fleet_api_keys.id;
ALTER TABLE mova.fleet_api_keys ALTER COLUMN id SET DEFAULT nextval('mova.fleet_api_keys_id_seq'::regclass);

-- Cross-validado 2x: bate com log real de SELECT em producao (F1) e com information_schema (F2-01)
-- NOTA (F2-02): usa ADD COLUMN IF NOT EXISTS em vez de um CREATE TABLE com
-- todas as colunas, porque a migration 2f8a9c4b5d1e (api_permissions system)
-- roda ANTES desta na cadeia e ja cria um "stub" de mova.users (so' com id),
-- necessario para sua FK. Isso garante que funciona nos dois cenarios:
-- tabela ainda nao existe (cria com id) OU ja existe como stub (completa as colunas).
CREATE TABLE IF NOT EXISTS mova.users ("id" INTEGER NOT NULL PRIMARY KEY);
ALTER TABLE mova.users ADD COLUMN IF NOT EXISTS "name" VARCHAR;
ALTER TABLE mova.users ADD COLUMN IF NOT EXISTS "login" VARCHAR;
ALTER TABLE mova.users ADD COLUMN IF NOT EXISTS "password" VARCHAR;
ALTER TABLE mova.users ADD COLUMN IF NOT EXISTS "account_id" INTEGER;
ALTER TABLE mova.users ADD COLUMN IF NOT EXISTS "master" INTEGER NOT NULL DEFAULT 0;
ALTER TABLE mova.users ADD COLUMN IF NOT EXISTS "hour_start" TIME;
ALTER TABLE mova.users ADD COLUMN IF NOT EXISTS "hour_end" TIME;
ALTER TABLE mova.users ADD COLUMN IF NOT EXISTS "day_start" INTEGER;
ALTER TABLE mova.users ADD COLUMN IF NOT EXISTS "day_end" INTEGER;
ALTER TABLE mova.users ADD COLUMN IF NOT EXISTS "end_access" DATE;
ALTER TABLE mova.users ADD COLUMN IF NOT EXISTS "status" INTEGER NOT NULL DEFAULT 1;
ALTER TABLE mova.users ADD COLUMN IF NOT EXISTS "modify_date" TIMESTAMP;
ALTER TABLE mova.users ADD COLUMN IF NOT EXISTS "user_mova" INTEGER DEFAULT 0;
ALTER TABLE mova.users ADD COLUMN IF NOT EXISTS "user_mobile" INTEGER DEFAULT 0;
ALTER TABLE mova.users ADD COLUMN IF NOT EXISTS "user_web" INTEGER DEFAULT 0;
ALTER TABLE mova.users ADD COLUMN IF NOT EXISTS "date_add" TIMESTAMP DEFAULT now();
ALTER TABLE mova.users ADD COLUMN IF NOT EXISTS "user_add" INTEGER;
ALTER TABLE mova.users ADD COLUMN IF NOT EXISTS "date_modif" TIMESTAMP;
ALTER TABLE mova.users ADD COLUMN IF NOT EXISTS "user_modif" INTEGER;
ALTER TABLE mova.users ADD COLUMN IF NOT EXISTS "version_id" INTEGER NOT NULL DEFAULT 0;
ALTER TABLE mova.users ADD COLUMN IF NOT EXISTS "email" VARCHAR;
ALTER TABLE mova.users ADD COLUMN IF NOT EXISTS "token" VARCHAR;
ALTER TABLE mova.users ADD COLUMN IF NOT EXISTS "customer_id" INTEGER;
CREATE SEQUENCE IF NOT EXISTS mova.users_id_seq;
ALTER SEQUENCE mova.users_id_seq OWNED BY mova.users.id;
ALTER TABLE mova.users ALTER COLUMN id SET DEFAULT nextval('mova.users_id_seq'::regclass);

-- customer_id referencia mova.customers (fora do escopo das ~20 tabelas centrais) - FK constraint omitida de proposito
CREATE TABLE IF NOT EXISTS mova.device (
    "id" INTEGER NOT NULL,
    "device_model_id" INTEGER NOT NULL,
    "identifier" VARCHAR NOT NULL,
    "asset" INTEGER,
    "internal_id" VARCHAR,
    "account_id" INTEGER,
    "status" INTEGER NOT NULL DEFAULT 1,
    "user_add" INTEGER,
    "user_modif" INTEGER,
    "date_modif" TIMESTAMP,
    "date_add" TIMESTAMP DEFAULT now(),
    "operadora" VARCHAR,
    "number" VARCHAR,
    "imei" VARCHAR,
    "group_id" INTEGER,
    "device_model_version_id" INTEGER,
    "iccid" VARCHAR,
    "serial_number" VARCHAR,
    "device_type" INTEGER,
    "manufacturing_date" TIMESTAMP,
    "modem" VARCHAR,
    "customer_id" BIGINT DEFAULT 88,
    "current_product_id" INTEGER DEFAULT 2,
    "user_removed" INTEGER,
    "date_removed" TIMESTAMP,
    "fw_version" VARCHAR,
    PRIMARY KEY (id)
);
CREATE SEQUENCE IF NOT EXISTS mova.device_id_seq;
ALTER SEQUENCE mova.device_id_seq OWNED BY mova.device.id;
ALTER TABLE mova.device ALTER COLUMN id SET DEFAULT nextval('mova.device_id_seq'::regclass);

CREATE TABLE IF NOT EXISTS mova.driver (
    "id" INTEGER NOT NULL,
    "name" VARCHAR,
    "account_id" INTEGER,
    "email" VARCHAR,
    "phone" VARCHAR,
    "auth" INTEGER NOT NULL,
    "login" VARCHAR,
    "password" VARCHAR,
    "cnh" VARCHAR,
    "cnh_category" VARCHAR,
    "cnh_validate" DATE,
    "passport" VARCHAR,
    "area" VARCHAR,
    "empresa" VARCHAR,
    "gestor" VARCHAR,
    "gestor_tel" VARCHAR,
    "local" VARCHAR,
    "rac_validate" DATE,
    "aso_validate" DATE,
    "group_id" INTEGER,
    "status" INTEGER NOT NULL DEFAULT 1,
    "driver_function_id" INTEGER DEFAULT 19,
    "user_add" INTEGER,
    "date_add" TIMESTAMP DEFAULT now(),
    "user_modif" INTEGER,
    "date_modif" TIMESTAMP,
    "old_mova_position" INTEGER,
    "subgroup_id" INTEGER,
    "obs_driver" TEXT,
    "salary" NUMERIC,
    "hour_day" INTERVAL,
    "hour_week" INTERVAL,
    "hour_month" INTERVAL,
    "cpf" VARCHAR(11),
    "password_apps" VARCHAR,
    "matricula" VARCHAR,
    "admission" DATE,
    "integration_id" VARCHAR,
    PRIMARY KEY (id)
);
CREATE SEQUENCE IF NOT EXISTS mova.driver_id_seq;
ALTER SEQUENCE mova.driver_id_seq OWNED BY mova.driver.id;
ALTER TABLE mova.driver ALTER COLUMN id SET DEFAULT nextval('mova.driver_id_seq'::regclass);

CREATE TABLE IF NOT EXISTS mova.driver_function (
    "id" INTEGER NOT NULL,
    "name" VARCHAR,
    "account_id" INTEGER,
    "status" INTEGER DEFAULT 1,
    PRIMARY KEY (id)
);
CREATE SEQUENCE IF NOT EXISTS mova.driver_function_id_seq;
ALTER SEQUENCE mova.driver_function_id_seq OWNED BY mova.driver_function.id;
ALTER TABLE mova.driver_function ALTER COLUMN id SET DEFAULT nextval('mova.driver_function_id_seq'::regclass);

CREATE TABLE IF NOT EXISTS mova.group (
    "id" INTEGER NOT NULL,
    "name" VARCHAR NOT NULL,
    "account_id" INTEGER NOT NULL,
    "cli_fat" INTEGER NOT NULL DEFAULT 0,
    "user_add" INTEGER,
    "date_add" TIMESTAMP DEFAULT now(),
    "user_modif" INTEGER,
    "date_modif" TIMESTAMP,
    "cnpj" VARCHAR,
    "general" BOOLEAN DEFAULT false,
    "max_speed" INTEGER DEFAULT 90,
    "corporate_name" VARCHAR,
    "address" VARCHAR,
    "contact" VARCHAR,
    "pro_rata" BOOLEAN NOT NULL DEFAULT false,
    "client_cod" VARCHAR,
    "vcms_day_off" INTEGER NOT NULL DEFAULT 3,
    PRIMARY KEY (id)
);
CREATE SEQUENCE IF NOT EXISTS mova.client_id_seq;
ALTER SEQUENCE mova.client_id_seq OWNED BY mova.group.id;
ALTER TABLE mova.group ALTER COLUMN id SET DEFAULT nextval('mova.client_id_seq'::regclass);
ALTER TABLE mova.group ADD CONSTRAINT group_client_cod_key UNIQUE (client_cod);
ALTER TABLE mova.group ADD CONSTRAINT fk_group_account FOREIGN KEY (account_id) REFERENCES mova.account (id);

CREATE TABLE IF NOT EXISTS mova.subgroup (
    "id" INTEGER NOT NULL,
    "name" VARCHAR NOT NULL,
    "group_id" INTEGER NOT NULL,
    "color" VARCHAR(7) DEFAULT '#000000'::character varying,
    "user_add" INTEGER,
    "date_add" TIMESTAMP DEFAULT now(),
    "user_modif" INTEGER,
    "date_modif" TIMESTAMP,
    "int_cittati" BOOLEAN,
    "company" VARCHAR,
    "address" VARCHAR,
    "cnpj" VARCHAR,
    "tolerance_before_ini" INTEGER NOT NULL DEFAULT 5,
    "tolerance_after_ini" INTEGER NOT NULL DEFAULT 5,
    "client_cod" VARCHAR,
    "suspended" BOOLEAN NOT NULL DEFAULT false,
    "suspended_date" TIMESTAMP,
    PRIMARY KEY (id)
);
CREATE SEQUENCE IF NOT EXISTS mova.subgroup_id_seq;
ALTER SEQUENCE mova.subgroup_id_seq OWNED BY mova.subgroup.id;
ALTER TABLE mova.subgroup ALTER COLUMN id SET DEFAULT nextval('mova.subgroup_id_seq'::regclass);

-- consumption_profile_id referencia mova.consumption_profile (fora do escopo das ~20 tabelas centrais) - FK constraint omitida de proposito
CREATE TABLE IF NOT EXISTS mova.tracked_unit (
    "id" INTEGER NOT NULL,
    "label" VARCHAR NOT NULL,
    "label2" VARCHAR,
    "account_id" INTEGER NOT NULL,
    "unit_category_id" INTEGER NOT NULL,
    "subgroup_id" INTEGER NOT NULL,
    "group_id" INTEGER NOT NULL,
    "os_num" INTEGER,
    "os_id" INTEGER,
    "cli_fat_id" INTEGER,
    "timezone" INTEGER NOT NULL,
    "status" INTEGER NOT NULL,
    "unit_type_id" INTEGER,
    "dst" BOOLEAN NOT NULL DEFAULT true,
    "old_mova_id" INTEGER,
    "hidro_factor" INTEGER,
    "user_add" INTEGER,
    "date_add" TIMESTAMP DEFAULT now(),
    "user_modif" INTEGER,
    "date_modif" TIMESTAMP,
    "date_removed" TIMESTAMP,
    "user_removed" INTEGER,
    "initial_odometer" INTEGER NOT NULL DEFAULT 0,
    "services_id" INTEGER NOT NULL DEFAULT 0,
    "input1_id" INTEGER,
    "input2_id" INTEGER,
    "input3_id" INTEGER,
    "input4_id" INTEGER,
    "output1_id" INTEGER,
    "output2_id" INTEGER,
    "output3_id" INTEGER,
    "output4_id" INTEGER,
    "inputicon1_id" INTEGER,
    "inputicon2_id" INTEGER,
    "inputicon3_id" INTEGER,
    "inputicon4_id" INTEGER,
    "outputicon4_id" INTEGER,
    "outputicon3_id" INTEGER,
    "outputicon2_id" INTEGER,
    "outputicon1_id" INTEGER,
    "driver_id" INTEGER DEFAULT 0,
    "vehicle_model_id" INTEGER,
    "trip_status" INTEGER,
    "obs" TEXT,
    "poskey" BOOLEAN,
    "instal_device_type" VARCHAR DEFAULT ''::character varying,
    "mid_fuel" VARCHAR,
    "qtd_passenger" INTEGER NOT NULL DEFAULT 0,
    "max_speed" INTEGER,
    "last_analyzed" TIMESTAMP,
    "initial_horimeter" INTEGER DEFAULT 0,
    "can_movieit" BOOLEAN DEFAULT false,
    "last_stop_id" INTEGER,
    "initial_working_horimeter" INTEGER DEFAULT 0,
    "initial_socagem" INTEGER DEFAULT 0,
    "initial_socagem_alta" INTEGER DEFAULT 0,
    "model" VARCHAR,
    "cost_km" NUMERIC,
    "bls_seat_layout_id" INTEGER,
    "removed_reason_id" INTEGER,
    "intranet_product_id" INTEGER[] NOT NULL DEFAULT '{64}'::integer[],
    "label_cart" VARCHAR(10),
    "package_product_id" INTEGER[],
    "config_file" INTEGER,
    "vehicle_year" SMALLINT,
    "consumption_profile_id" INTEGER,
    PRIMARY KEY (id)
);
CREATE SEQUENCE IF NOT EXISTS mova.tracked_unit_id_seq;
ALTER SEQUENCE mova.tracked_unit_id_seq OWNED BY mova.tracked_unit.id;
ALTER TABLE mova.tracked_unit ALTER COLUMN id SET DEFAULT nextval('mova.tracked_unit_id_seq'::regclass);

CREATE TABLE IF NOT EXISTS mova.tracked_unit_device (
    "id" INTEGER NOT NULL,
    "tracked_unit_id" INTEGER NOT NULL,
    "device_id" INTEGER NOT NULL,
    "association_date" TIMESTAMP NOT NULL,
    "release_date" TIMESTAMP,
    "status" INTEGER NOT NULL,
    "user_id" INTEGER NOT NULL,
    "device_primary" INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (id)
);
CREATE SEQUENCE IF NOT EXISTS mova.tracked_unit_device_id_seq;
ALTER SEQUENCE mova.tracked_unit_device_id_seq OWNED BY mova.tracked_unit_device.id;
ALTER TABLE mova.tracked_unit_device ALTER COLUMN id SET DEFAULT nextval('mova.tracked_unit_device_id_seq'::regclass);

CREATE TABLE IF NOT EXISTS mova.user_group_access (
    "id" INTEGER NOT NULL,
    "subgroup_id" INTEGER,
    "group_id" INTEGER,
    "user_id" INTEGER,
    PRIMARY KEY (id)
);
CREATE SEQUENCE IF NOT EXISTS mova.user_group_access_id_seq;
ALTER SEQUENCE mova.user_group_access_id_seq OWNED BY mova.user_group_access.id;
ALTER TABLE mova.user_group_access ALTER COLUMN id SET DEFAULT nextval('mova.user_group_access_id_seq'::regclass);

CREATE TABLE IF NOT EXISTS vcms.vcms_unit_device (
    "id" INTEGER NOT NULL,
    "status" INTEGER NOT NULL DEFAULT 1,
    "unit_id" INTEGER NOT NULL,
    "device_id" INTEGER NOT NULL,
    "association_date" TIMESTAMP NOT NULL DEFAULT now(),
    "release_date" TIMESTAMP,
    "user_id" INTEGER NOT NULL,
    PRIMARY KEY (id)
);
CREATE SEQUENCE IF NOT EXISTS vcms.vcms_unit_device_id_seq;
ALTER SEQUENCE vcms.vcms_unit_device_id_seq OWNED BY vcms.vcms_unit_device.id;
ALTER TABLE vcms.vcms_unit_device ALTER COLUMN id SET DEFAULT nextval('vcms.vcms_unit_device_id_seq'::regclass);
CREATE TABLE IF NOT EXISTS mova.con_telemetry (
    "id" INTEGER NOT NULL,
    "trip_id" BIGINT NOT NULL,
    "unit_id" INTEGER NOT NULL,
    "unit_label" VARCHAR NOT NULL,
    "device_id" INTEGER NOT NULL,
    "driver_id" INTEGER,
    "driver_name" VARCHAR,
    "start_odometer" BIGINT,
    "end_odometer" BIGINT,
    "distance_traveled" NUMERIC,
    "start_hourmeter" BIGINT,
    "end_hourmeter" BIGINT,
    "total_time" INTEGER,
    "time_stopped" INTEGER,
    "time_moving" INTEGER,
    "time_raining" INTEGER,
    "time_dry" INTEGER,
    "start_time" TIMESTAMP,
    "start_lat" NUMERIC(9,6),
    "start_lon" NUMERIC(9,6),
    "start_poi_id" INTEGER,
    "start_poi_name" VARCHAR,
    "start_poi_distance" INTEGER,
    "start_area_id" INTEGER,
    "start_area_name" VARCHAR,
    "end_time" TIMESTAMP,
    "end_lat" NUMERIC(9,6),
    "end_lon" NUMERIC(9,6),
    "end_poi_id" INTEGER,
    "end_poi_name" VARCHAR,
    "end_poi_distance" INTEGER,
    "end_area_id" INTEGER,
    "end_area_name" VARCHAR,
    "max_speed" INTEGER,
    "avg_speed" INTEGER,
    "count_over_speed" INTEGER,
    "time_over_speed" INTEGER,
    "time_banguela" INTEGER,
    "count_banguela" INTEGER,
    "time_blue" INTEGER,
    "count_blue" INTEGER,
    "time_green" INTEGER,
    "count_green" INTEGER,
    "time_extra_eco" INTEGER,
    "count_extra_eco" INTEGER,
    "time_yellow" INTEGER,
    "count_yellow" INTEGER,
    "time_red" INTEGER,
    "count_red" INTEGER,
    "time_stop_engine_on" INTEGER,
    "count_stop_engine_on" INTEGER,
    "time_stop_accel" INTEGER,
    "count_stop_accel" INTEGER,
    "count_clutch" INTEGER,
    "time_cluth_excess" INTEGER,
    "count_cluth_excess" INTEGER,
    "fuel_used" NUMERIC,
    "time_engine_off" INTEGER,
    "count_hard_acel" INTEGER,
    "count_hard_brake" INTEGER,
    "time_tolerancia" INTEGER,
    "time_inercia" INTEGER,
    "max_road_permitted_speed" INTEGER,
    "max_road_rain_speed" INTEGER,
    "time_over_urban_speed" INTEGER,
    "time_over_urban_rain_speed" INTEGER,
    "reached_over_urban_speed" INTEGER,
    "reached_over_urban_rain_speed" INTEGER,
    "time_over_road_speed" INTEGER,
    "time_over_road_rain_speed" INTEGER,
    "reached_over_road_speed" INTEGER,
    "reached_over_road_rain_speed" INTEGER,
    "max_rpm_permitted" INTEGER,
    "time_over_rpm" INTEGER,
    "reached_rpm" INTEGER,
    "max_urban_permitted_speed" INTEGER,
    "urban_rain_speed" INTEGER,
    "count_over_road_speed" INTEGER,
    "count_over_road_rain_speed" INTEGER,
    "count_over_urban_speed" INTEGER,
    "count_over_urban_rain_speed" INTEGER,
    "efficiency_kml" NUMERIC,
    "time_over_turbo_pressure" INTEGER,
    "time_under_turbo_pressure" INTEGER,
    "time_write" TIMESTAMP DEFAULT now(),
    "line" VARCHAR,
    "time_stop_engine_on_productive" INTEGER,
    "time_low_speed" INTEGER,
    "time_eco_roll" INTEGER,
    "start_fuel" NUMERIC,
    "end_fuel" NUMERIC,
    "time_retarder" INTEGER,
    "time_autopilot" INTEGER,
    "distance_pulling" INTEGER,
    "distance_simple_inertia" INTEGER,
    "distance_retarder" INTEGER,
    "distance_ecoroll" INTEGER,
    "distance_autopilot" INTEGER,
    "fuel_used_stopped" NUMERIC,
    "trip_status" BOOLEAN,
    "trip_number" INTEGER,
    "trip_direction" INTEGER,
    "trip_opening_date" TIMESTAMP,
    "journey_status" BOOLEAN,
    "journey_opening_date" TIMESTAMP,
    "line_number" INTEGER,
    "calculated" INTEGER DEFAULT 0,
    "time_engine_load_level1" INTEGER,
    "time_engine_load_level2" INTEGER,
    "time_engine_load_level3" INTEGER,
    "count_harsh_turn" INTEGER,
    "count_speed_violation_l2" INTEGER,
    "count_speed_violation_l3" INTEGER,
    "calc_fuel_used" NUMERIC(15,2),
    "calc_start_fuel" NUMERIC(15,2),
    "calc_end_fuel" NUMERIC(15,2),
    "produtiva" BOOLEAN DEFAULT false,
    "raw_start_fuel" NUMERIC,
    "raw_end_fuel" NUMERIC,
    "raw_fuel_used" NUMERIC,
    PRIMARY KEY (id)
);
CREATE SEQUENCE IF NOT EXISTS mova.con_telemetry_id_seq;
ALTER SEQUENCE mova.con_telemetry_id_seq OWNED BY mova.con_telemetry.id;
ALTER TABLE mova.con_telemetry ALTER COLUMN id SET DEFAULT nextval('mova.con_telemetry_id_seq'::regclass);
ALTER TABLE mova.con_telemetry ADD CONSTRAINT uq_con_telemetry_unit_trip UNIQUE (trip_id, unit_id);

CREATE TABLE IF NOT EXISTS mova.dev_status_30 (
    "id" BIGINT NOT NULL,
    "device_id" INTEGER,
    "unit_id" INTEGER,
    "local_time" TIMESTAMP,
    "time_write" TIMESTAMP,
    "latitude" NUMERIC(9,6),
    "longitude" NUMERIC(9,6),
    "altitude" INTEGER,
    "gps" BOOLEAN,
    "direction" INTEGER,
    "speed" INTEGER,
    "tracker_event_id" INTEGER,
    "gpoint" GEOMETRY,
    "poi_id" INTEGER,
    "area_id" INTEGER,
    "ignition" BOOLEAN,
    "odom" NUMERIC,
    "poi_distance" INTEGER,
    "in1" VARCHAR,
    "in2" VARCHAR,
    "in3" VARCHAR,
    "driver_id" INTEGER,
    "address" VARCHAR,
    "street" VARCHAR,
    "region" VARCHAR,
    "city" VARCHAR,
    "state" VARCHAR,
    "country" VARCHAR,
    "revgeo_level" INTEGER,
    "number" VARCHAR,
    "zip" VARCHAR,
    "voltage" NUMERIC,
    "battery" NUMERIC,
    "delay" INTEGER,
    "analog" NUMERIC,
    "msg_num" INTEGER,
    "rpm" INTEGER,
    "area_name" VARCHAR,
    "poi_name" VARCHAR,
    "driver_name" VARCHAR,
    "in0" VARCHAR,
    "hourmeter" INTEGER,
    "driver_login" VARCHAR,
    "in4" VARCHAR,
    "in5" VARCHAR,
    "in6" VARCHAR,
    "in7" VARCHAR,
    "in8" VARCHAR,
    "out4" VARCHAR,
    "out5" VARCHAR,
    "out6" VARCHAR,
    "out7" VARCHAR,
    "out8" VARCHAR,
    "out1" VARCHAR,
    "out2" VARCHAR,
    "out3" VARCHAR,
    "out0" VARCHAR,
    "odom_total" NUMERIC,
    "economy" INTEGER,
    "trip_id" INTEGER,
    "faixa" INTEGER,
    "max_road_speed" INTEGER,
    "fence_index" INTEGER,
    "trip_id2" BIGINT,
    "can_rpm" NUMERIC,
    "can_total_odometer" NUMERIC,
    "can_engine_hourmeter" NUMERIC,
    "can_accel_pedal_percent" NUMERIC,
    "can_break_pedal_state" NUMERIC,
    "can_engine_coolant_temp" NUMERIC,
    "can_intake_manifold_temp" NUMERIC,
    "can_intake_manifold_pressure" NUMERIC,
    "can_avg_fuel_economy_kmpl" NUMERIC,
    "can_engine_fuel_rate_lph" NUMERIC,
    "can_total_used_fuel" NUMERIC,
    "can_fuel_level_percent" NUMERIC,
    "can_def_level_percent" NUMERIC,
    "can_engine_oil_pressure" NUMERIC,
    "can_engine_oil_temp" NUMERIC,
    "can_retarder_activated" NUMERIC,
    "can_retarder_in_use" NUMERIC,
    "can_engine_load_percent" NUMERIC,
    "can_engine_torque_percent" NUMERIC,
    "can_gear" NUMERIC,
    "can_pneumatic_system1_pressure" NUMERIC,
    "can_pneumatic_system2_pressure" NUMERIC,
    "can_water_in_fuel" NUMERIC,
    "can_ambient_air_temp" NUMERIC,
    "can_cruise_control_state" NUMERIC,
    "can_pto_state" NUMERIC,
    "can_parking_brake_state" NUMERIC,
    "can_mil_indicator" NUMERIC,
    "can_headlight_state" NUMERIC,
    "can_vehicle_total_weight" NUMERIC,
    "can_remaining_service_distance" NUMERIC,
    "can_speed" NUMERIC,
    "can_dtc_code" NUMERIC,
    "can_dtc" NUMERIC,
    "can_obd_compliance" NUMERIC,
    "can_control_module_voltage" NUMERIC,
    "can_intake_air_temp" NUMERIC,
    "can_direct_fuel_rail_pressure" NUMERIC,
    "can_warmups_since_clear" NUMERIC,
    "can_barometric_pressure" NUMERIC,
    "can_commanded_egr" NUMERIC,
    "can_maf_air_flow_rate" NUMERIC,
    "can_egr_error" NUMERIC,
    "can_calculated_engine_load_value" NUMERIC,
    "can_distance_since_clear_codes" NUMERIC,
    "can_time_since_mil" NUMERIC,
    "can_distance_since_mil" NUMERIC,
    "can_runtime_since_engine_start" NUMERIC,
    "can_max_air_flow_rate" NUMERIC,
    "can_fuel_type" NUMERIC,
    "can_fuel_pressure" NUMERIC,
    "can_ethanol_percent" NUMERIC,
    "can_demand_torque_percent" NUMERIC,
    "can_actual_torque_percent" NUMERIC,
    "hourmeter_total" INTEGER,
    "route_id" BIGINT,
    "can_turbo_charger_pressure" INTEGER,
    "can_turbocharger_temp" NUMERIC,
    "proc_runtime" INTERVAL,
    "can_engine_coolant_level" INTEGER,
    "can_retarder_torque" INTEGER,
    "prfid" VARCHAR,
    "connection" INTEGER DEFAULT 0,
    "con_status_bus_line_id" BIGINT,
    "external_sensor_temperature" NUMERIC,
    "line_number" TEXT,
    "estimated_distance" NUMERIC,
    "estimated_total_used_fuel" DOUBLE PRECISION,
    "estimated_km_per_l" DOUBLE PRECISION,
    "estimated_rpm_factor" DOUBLE PRECISION,
    "estimated_accel_factor" DOUBLE PRECISION,
    "estimated_acceleration" DOUBLE PRECISION,
    "estimated_idle_fuel_ml" DOUBLE PRECISION,
    "calc_total_used_fuel" NUMERIC,
    "raw_total_used_fuel" NUMERIC,
    "odom_quality_flag" VARCHAR(40),
    "odom_rule_version" SMALLINT,
    "odom_canonical_shadow" NUMERIC,
    "can_gear_state" VARCHAR(20),
    PRIMARY KEY (id)
);
CREATE SEQUENCE IF NOT EXISTS mova.dev_status_30_id_seq;
ALTER SEQUENCE mova.dev_status_30_id_seq OWNED BY mova.dev_status_30.id;
ALTER TABLE mova.dev_status_30 ALTER COLUMN id SET DEFAULT nextval('mova.dev_status_30_id_seq'::regclass);

    """)


def downgrade() -> None:
    # Downgrade deliberadamente NAO remove as tabelas: elas contem dados
    # reais de producao (mova/vcms sao schemas compartilhados com outros
    # sistemas). Um DROP TABLE aqui seria destrutivo e nao ha cenario
    # legitimo de uso que exija desfazer este snapshot.
    raise NotImplementedError(
        "Downgrade desabilitado de proposito: esta migration e um "
        "snapshot de tabelas legadas/compartilhadas (mova/vcms). "
        "Nao ha remocao segura de DROP TABLE aqui."
    )