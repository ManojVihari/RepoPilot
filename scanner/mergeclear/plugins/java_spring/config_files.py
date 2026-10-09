"""
Spring configuration (application*.yml / *.properties / bootstrap*) per module.

Values are flattened to dotted keys, placeholders are resolved where
possible and secrets are masked before anything leaves the scanner.
"""
import logging
import os
import re

from mergeclear.core.files import SKIP_DIRS

logger = logging.getLogger(__name__)

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is a declared dependency
    yaml = None

CONFIG_NAME = re.compile(r"^(application|bootstrap)(-([\w.-]+))?\.(ya?ml|properties)$")
SECRET_KEY = re.compile(
    r"(password|passwd|pwd|secret|token|credential|private[-_.]?key|api[-_.]?key|access[-_.]?key|"
    r"secret[-_.]?key|client[-_.]?secret|signing[-_.]?key|jwt[-_.]?key)",
    re.I,
)
URL_CREDENTIALS = re.compile(r"(\w+://[^:/@\s]+:)([^@\s]+)(@)")
PLACEHOLDER = re.compile(r"\$\{([^}:]+)(?::([^}]*))?\}")
MASK = "****"


class ModuleConfig:
    def __init__(self):
        self.files = []
        self.default = {}
        self.profiles = {}

    def get(self, key, profile=None):
        if profile and key in self.profiles.get(profile, {}):
            return self.profiles[profile][key]
        return self.default.get(key)

    def resolve(self, text, depth=0):
        """Replace ${key:default} placeholders with configured values."""
        if not isinstance(text, str) or "${" not in text or depth > 5:
            return text

        def sub(match):
            value = self.default.get(match.group(1))
            if value is None:
                for props in self.profiles.values():
                    if match.group(1) in props:
                        value = props[match.group(1)]
                        break
            if value is None:
                return match.group(2) if match.group(2) is not None else match.group(0)
            return str(value)

        resolved = PLACEHOLDER.sub(sub, text)
        return self.resolve(resolved, depth + 1) if resolved != text else resolved

    def all_values(self, key):
        """[(profile or 'default', value)] for a key across profiles."""
        out = []
        if key in self.default:
            out.append(("default", self.default[key]))
        for profile, props in sorted(self.profiles.items()):
            if key in props:
                out.append((profile, props[key]))
        return out

    def keys(self):
        keys = set(self.default)
        for props in self.profiles.values():
            keys |= set(props)
        return keys


def mask_value(key, value):
    if value is None:
        return None
    text = str(value)
    is_location = re.search(r"(uri|url|endpoint|path|location|host)$", key, re.I)
    if SECRET_KEY.search(key) and not is_location and not text.startswith("${"):
        return MASK
    return URL_CREDENTIALS.sub(lambda m: m.group(1) + MASK + m.group(3), text)


def masked(props):
    return {k: mask_value(k, v) for k, v in sorted(props.items())}


# ============================
# LOADING
# ============================

def load_module_configs(repo_path, module_dirs, module_of):
    """{module_dir: ModuleConfig} from config files under each module."""
    configs = {d: ModuleConfig() for d in module_dirs}
    central = []   # (rel_path, file_name) of config-server style files

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        rel_root = os.path.relpath(root, repo_path).replace(os.sep, "/")

        if "/src/test" in f"/{rel_root}" or "/src/it" in f"/{rel_root}":
            continue

        for f in files:
            if not f.endswith((".yml", ".yaml", ".properties")):
                continue

            rel = f if rel_root == "." else f"{rel_root}/{f}"
            in_resources = "resources" in rel_root.split("/")
            # Spring Boot reads config from the classpath root, classpath:/config
            # and the working directory (module root or ./config)
            is_boot_location = (
                rel_root.endswith(("src/main/resources", "src/main/resources/config"))
                or rel_root == "." or rel_root == "config"
                or rel_root in configs or rel_root.endswith("/config") and rel_root[:-7] in configs
            )

            if CONFIG_NAME.match(f) and is_boot_location:
                _load_into(configs.setdefault(module_of(rel), ModuleConfig()), repo_path, rel, f)
            elif in_resources:
                central.append((rel, f))

    _apply_central_configs(repo_path, configs, central)

    for config in configs.values():
        _resolve_all(config)

    return configs


def _load_into(config, repo_path, rel, filename, profile_override=None):
    match = CONFIG_NAME.match(filename)
    file_profile = profile_override or (match.group(3) if match else None)

    try:
        documents = _read_documents(os.path.join(repo_path, rel))
    except Exception as e:
        logger.debug("Failed to read config %s: %s", rel, e)
        return

    config.files.append(rel)

    for props in documents:
        profile = (
            props.pop("spring.config.activate.on-profile", None)
            or props.pop("spring.profiles", None)
            or file_profile
        )

        if profile and profile != "default":
            for p in str(profile).replace("!", "").split(","):
                config.profiles.setdefault(p.strip(), {}).update(props)
        else:
            config.default.update(props)


def _apply_central_configs(repo_path, configs, central):
    """Config-server style files: shared application.yml and <app-name>.yml."""
    by_app_name = {}
    for module_dir, config in configs.items():
        name = config.default.get("spring.application.name") or os.path.basename(module_dir)
        by_app_name[str(name)] = config

    clients = [
        c for c in configs.values()
        if any(k.startswith("spring.cloud.config.") and not k.startswith("spring.cloud.config.server.") for k in c.default)
        or "configserver" in str(c.default.get("spring.config.import", ""))
    ]

    # shared application*.yml first, app-specific files override it
    for rel, filename in sorted(central, key=lambda x: not x[1].startswith("application")):
        stem = filename.rsplit(".", 1)[0]

        if stem == "application" or stem.startswith("application-"):
            profile = stem[len("application-"):] or None
            for config in clients:
                _load_into(config, repo_path, rel, filename, profile_override=profile)
            continue

        # <app-name>.yml or <app-name>-<profile>.yml
        for name in sorted(by_app_name, key=len, reverse=True):
            if stem == name or stem.startswith(name + "-"):
                profile = stem[len(name) + 1:] or None
                _load_into(by_app_name[name], repo_path, rel, filename, profile_override=profile)
                break


def _read_documents(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()

    if path.endswith(".properties"):
        return [_parse_properties(text)]

    if yaml is None:
        return []

    docs = []
    for doc in yaml.safe_load_all(text):
        if isinstance(doc, dict):
            flat = {}
            _flatten(doc, "", flat)
            docs.append(flat)
    return docs


def _parse_properties(text):
    props = {}
    lines = text.splitlines()
    i = 0

    while i < len(lines):
        line = lines[i].strip()
        while line.endswith("\\") and i + 1 < len(lines):
            i += 1
            line = line[:-1] + lines[i].strip()
        i += 1

        if not line or line.startswith(("#", "!")):
            continue

        match = re.match(r"([^=:\s]+)\s*[=:\s]\s*(.*)", line)
        if match:
            props[match.group(1)] = match.group(2)

    return props


def _flatten(value, prefix, out):
    if isinstance(value, dict):
        for k, v in value.items():
            _flatten(v, f"{prefix}.{k}" if prefix else str(k), out)
    elif isinstance(value, list):
        if all(not isinstance(v, (dict, list)) for v in value):
            out[prefix] = ",".join("" if v is None else str(v) for v in value)
        for i, v in enumerate(value):
            _flatten(v, f"{prefix}[{i}]", out)
    else:
        out[prefix] = "" if value is None else value


def _resolve_all(config):
    for props in [config.default] + list(config.profiles.values()):
        for k, v in list(props.items()):
            if isinstance(v, str) and "${" in v:
                props[k] = config.resolve(v)


# ============================
# DERIVED INFRASTRUCTURE
# ============================

JDBC_URL = re.compile(r"jdbc:(?P<type>[\w:]+?):(?://(?P<host>[^/:;?]+)(?::(?P<port>\d+))?/?(?P<db>[^?;]*))?")


def parse_jdbc_url(url):
    match = JDBC_URL.match(url or "")
    if not match:
        return {"url": mask_value("url", url)}

    db_type = match.group("type").split(":")[0]
    info = {
        "type": {"postgresql": "postgresql", "mysql": "mysql", "mariadb": "mariadb",
                 "oracle": "oracle", "sqlserver": "sqlserver", "h2": "h2",
                 "hsqldb": "hsqldb", "derby": "derby", "sqlite": "sqlite"}.get(db_type, db_type),
        "url": mask_value("url", url),
    }

    if match.group("host"):
        info["host"] = match.group("host")
    if match.group("port"):
        info["port"] = int(match.group("port"))
    if match.group("db"):
        info["database"] = match.group("db")
    if db_type == "h2" and ":mem:" in url:
        info["in_memory"] = True

    return info


def derive_infrastructure(config: ModuleConfig):
    """Infrastructure facts (datastores, brokers, discovery, routes) from config."""
    out = {}

    def first(*keys):
        for key in keys:
            value = config.get(key)
            if value not in (None, ""):
                return value
        return None

    app_name = first("spring.application.name")
    if app_name:
        out["application_name"] = app_name

    port = first("server.port")
    if port is not None:
        out["server_port"] = port

    context_path = first("server.servlet.context-path", "server.context-path", "spring.webflux.base-path", "spring.mvc.servlet.path")
    if context_path:
        out["context_path"] = context_path

    active = first("spring.profiles.active")
    if active:
        out["active_profiles"] = [p.strip() for p in str(active).split(",") if p.strip()]

    if config.profiles:
        out["profiles"] = sorted(config.profiles)

    # relational datasources (default + per profile)
    datasources = []
    for key in ("spring.datasource.url", "spring.datasource.jdbc-url", "spring.r2dbc.url", "spring.datasource.hikari.jdbc-url"):
        for profile, url in config.all_values(key):
            info = parse_jdbc_url(str(url)) if str(url).startswith("jdbc:") else {"url": mask_value("url", url)}
            if str(url).startswith("r2dbc:"):
                info["type"] = str(url).split(":")[1]
            info["profile"] = profile
            datasources.append(info)
    if not datasources and config.get("spring.sql.init.platform"):
        datasources.append({"type": config.get("spring.sql.init.platform"), "profile": "default"})
    if datasources:
        out["datasources"] = datasources

    jpa = {k: v for k, v in (
        ("ddl_auto", first("spring.jpa.hibernate.ddl-auto")),
        ("dialect", first("spring.jpa.properties.hibernate.dialect", "spring.jpa.database-platform")),
        ("open_in_view", first("spring.jpa.open-in-view")),
    ) if v is not None}
    if jpa:
        out["jpa"] = jpa

    mongo_uri = first("spring.data.mongodb.uri", "spring.mongodb.uri")
    mongo_host = first("spring.data.mongodb.host")
    mongo_db = first("spring.data.mongodb.database")
    if mongo_uri or mongo_host or mongo_db:
        out["mongodb"] = {k: v for k, v in (
            ("uri", mask_value("uri", mongo_uri) if mongo_uri else None),
            ("host", mongo_host),
            ("port", first("spring.data.mongodb.port")),
            ("database", mongo_db),
        ) if v is not None}

    redis_host = first("spring.data.redis.host", "spring.redis.host")
    redis_url = first("spring.data.redis.url", "spring.redis.url")
    redis_cluster = first("spring.data.redis.cluster.nodes", "spring.redis.cluster.nodes")
    if redis_host or redis_url or redis_cluster:
        out["redis"] = {k: v for k, v in (
            ("host", redis_host),
            ("port", first("spring.data.redis.port", "spring.redis.port")),
            ("url", mask_value("url", redis_url) if redis_url else None),
            ("cluster_nodes", redis_cluster),
        ) if v is not None}

    cache_type = first("spring.cache.type")
    cache_names = first("spring.cache.cache-names")
    if cache_type or cache_names:
        out["cache"] = {k: v for k, v in (("type", cache_type), ("cache_names", cache_names)) if v}

    kafka = first("spring.kafka.bootstrap-servers", "spring.cloud.stream.kafka.binder.brokers")
    if kafka:
        out["kafka"] = {k: v for k, v in (
            ("bootstrap_servers", kafka),
            ("consumer_group", first("spring.kafka.consumer.group-id")),
        ) if v}

    rabbit = first("spring.rabbitmq.host", "spring.rabbitmq.addresses")
    if rabbit:
        out["rabbitmq"] = {k: v for k, v in (("host", rabbit), ("port", first("spring.rabbitmq.port")),
                                              ("virtual_host", first("spring.rabbitmq.virtual-host"))) if v}

    es = first("spring.elasticsearch.uris", "spring.elasticsearch.rest.uris", "spring.data.elasticsearch.cluster-nodes")
    if es:
        out["elasticsearch"] = {"uris": es}

    mail = first("spring.mail.host")
    if mail:
        out["mail"] = {"host": mail, "port": first("spring.mail.port")}

    eureka = first("eureka.client.serviceUrl.defaultZone", "eureka.client.service-url.defaultZone")
    if eureka or first("eureka.client.register-with-eureka") is not None:
        out["service_discovery"] = {"type": "eureka", "url": eureka}
    elif first("spring.cloud.consul.host"):
        out["service_discovery"] = {"type": "consul", "url": first("spring.cloud.consul.host")}

    config_server = first("spring.cloud.config.uri")
    config_import = first("spring.config.import")
    if config_server or (config_import and "configserver" in str(config_import)):
        out["config_server"] = str(config_server or config_import).replace("optional:", "").replace("configserver:", "")

    issuer = first(
        "spring.security.oauth2.resourceserver.jwt.issuer-uri",
        "spring.security.oauth2.resourceserver.jwt.jwk-set-uri",
        "security.oauth2.resource.user-info-uri",
        "security.oauth2.resource.token-info-uri",
    )
    if issuer:
        out["identity_provider"] = issuer

    routes = _indexed_groups(config, "spring.cloud.gateway.routes") or _indexed_groups(config, "spring.cloud.gateway.server.webflux.routes")
    if routes:
        out["gateway_routes"] = [
            {
                "id": r.get("id"),
                "uri": r.get("uri"),
                "predicates": _list_values(r, "predicates"),
                "filters": _list_values(r, "filters"),
            }
            for r in routes
        ]

    zuul = {}
    for key in config.keys():
        match = re.match(r"zuul\.routes\.([\w-]+)\.(path|url|serviceId|service-id|stripPrefix|strip-prefix)$", key)
        if match:
            zuul.setdefault(match.group(1), {})[match.group(2).replace("-", "_").replace("serviceId", "service_id")] = config.get(key)
    if zuul:
        out["gateway_routes"] = out.get("gateway_routes", []) + [{"id": k, **v} for k, v in sorted(zuul.items())]

    bindings = {}
    for key in config.keys():
        match = re.match(r"spring\.cloud\.stream\.bindings\.([\w-]+)\.(destination|group|binder|content-type)$", key)
        if match:
            bindings.setdefault(match.group(1), {})[match.group(2).replace("-", "_")] = config.get(key)
    if bindings:
        out["stream_bindings"] = bindings

    function_definition = first("spring.cloud.function.definition")
    if function_definition:
        out["stream_functions"] = [f.strip() for f in str(function_definition).replace("|", ";").split(";") if f.strip()]

    external = {}
    for key in sorted(config.keys()):
        value = config.get(key)
        if value is None:
            for _, v in config.all_values(key):
                value = v
                break
        if isinstance(value, str) and re.match(r"^(https?|wss?|grpc)://", value) and not key.startswith(("eureka.", "spring.cloud.config", "spring.datasource", "spring.security.oauth2.resourceserver")):
            external[key] = mask_value(key, value)
    if external:
        out["external_urls"] = external

    exposure = first("management.endpoints.web.exposure.include")
    if exposure:
        out["actuator_exposure"] = exposure

    return out


def _indexed_groups(config, prefix):
    """spring.x.routes[0].id, spring.x.routes[0].uri ... -> [{id, uri}, ...]"""
    groups = {}
    pattern = re.compile(re.escape(prefix) + r"\[(\d+)\]\.(.+)$")

    for key in config.keys():
        match = pattern.match(key)
        if match:
            groups.setdefault(int(match.group(1)), {})[match.group(2)] = config.get(key) if config.get(key) is not None else config.all_values(key)[0][1]

    return [groups[i] for i in sorted(groups)]


def _list_values(group, name):
    indexed = [v for k, v in sorted(group.items()) if re.fullmatch(rf"{name}\[\d+\]", k)]
    if indexed:
        return indexed
    value = group.get(name)
    return [v for v in str(value).split(",") if v] if value else []
