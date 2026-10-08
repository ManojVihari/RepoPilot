import shutil
import subprocess
from pathlib import Path

import pytest

from docai.plugins.java_spring.analyzer import parse_sql
from docai.plugins.java_spring.catalog import classify_library
from docai.plugins.java_spring.extractor import SpringExtractor

FIXTURE = Path(__file__).parent / "fixtures" / "spring_shop"


@pytest.fixture(scope="module")
def model():
    extractor = SpringExtractor()
    extractor.build_model(str(FIXTURE))
    return extractor


def endpoint(model, handler):
    return next(e for e in model.endpoints if e["handler"] == handler)


def facts(e, group):
    return e["integrations"].get(group, [])


# ============================
# ENDPOINTS
# ============================

def test_endpoints_and_paths(model):
    found = {(e["method"], e["path"]) for e in model.endpoints}

    assert found == {
        ("POST", "/api/orders"),
        ("GET", "/api/orders/{id}"),
        ("DELETE", "/api/orders/{id}"),
        ("GET", "/api/orders"),
    }
    assert all(e["context_path"] == "/shop" for e in model.endpoints)


def test_request_contract(model):
    create = endpoint(model, "OrderController.create")

    assert create["description"] == "Places a new order."
    assert create["consumes"] == ["application/json"]
    assert [(p["name"], p["in"]) for p in create["params"]] == [("request", "body"), ("X-Request-Id", "header")]

    body = create["request_body"]
    assert body["type"] == "CreateOrderRequest" and body["validated"]
    assert body["schema"]["customerEmail"]["validation"] == {"notEmpty": True, "required": True, "notBlank": True, "email": True}
    assert body["schema"]["paymentMethod"] == {"type": "PaymentMethod", "enum": ["CARD", "PAYPAL"]}

    items = body["schema"]["lines"]["schema"]["items"]
    assert items["name"] == "LineItem"
    assert items["fields"]["quantity"]["validation"] == {"minimum": 1}


def test_pageable_and_optional_params(model):
    params = {p["name"]: p for p in endpoint(model, "OrderController.list")["params"]}

    assert set(params) == {"page", "size", "sort", "status"}
    assert params["status"]["required"] is False


def test_status_codes_and_exceptions(model):
    codes = lambda handler: sorted({s["code"] for s in endpoint(model, handler)["status_codes"]})

    # 201 @ResponseStatus, 400 validation, 402 via @ExceptionHandler in advice
    assert codes("OrderController.create") == [201, 400, 402]
    # ResponseEntity builders + @ResponseStatus on exception thrown via orElseThrow
    assert codes("OrderController.get") == [200, 400, 404]
    assert codes("OrderController.cancel") == [200, 410]

    exceptions = endpoint(model, "OrderController.create")["exceptions"]
    assert exceptions == [{"exception": "PaymentDeclinedException", "thrown_in": "OrderServiceImpl.create",
                           "status": 402, "mapped_by": "ApiErrors.declined"}]


def test_security(model):
    get = endpoint(model, "OrderController.get")["security"]
    assert get["annotations"] == [{"annotation": "PreAuthorize", "expression": "hasRole('USER')", "level": "method"}]
    assert get["url_rule"]["access"] == "authenticated"

    cancel = endpoint(model, "OrderController.cancel")["security"]["url_rule"]
    assert cancel["access"] == "hasRole" and cancel["roles"] == ["ADMIN"]

    security = model.architecture["security"]
    assert security["mechanisms"] == ["jwt", "oauth2-resource-server", "spring-security"]
    assert security["csrf_disabled"] is True


# ============================
# DOWNSTREAM INTEGRATIONS
# ============================

def test_endpoint_reaches_every_integration_through_call_chain(model):
    create = endpoint(model, "OrderController.create")

    db = {(f["technology"], f["operation"], tuple(f["table"])) for f in facts(create, "databases")}
    assert db == {("jpa", "WRITE", ("orders",)), ("jdbc", "WRITE", ("audit_log",))}

    http = facts(create, "external_apis")
    assert [(f["http_method"], f["url"], f["host"]) for f in http] == [
        ("POST", "https://api.payments.example.com/v1/charges/{id}", "api.payments.example.com")
    ]

    messaging = {(f["technology"], f["destination"]) for f in facts(create, "messaging")}
    # SQS publish happens in the synchronous @EventListener of the published event
    assert messaging == {("kafka", "orders.created.v1"), ("sqs", "warehouse-picking")}

    caches = {(f["technology"], f["operation"]) for f in facts(create, "caches")}
    assert caches == {("redis", "WRITE"), ("spring-cache", "EVICT")}

    assert facts(create, "resilience")[0]["name"] == "payments"
    assert create["db_ops"][0]["type"] in ("WRITE",)
    assert create["call_graph"]["direct"] == ["OrderServiceImpl.create"]


def test_web_client_base_url_from_bean(model):
    http = model.architecture["integrations"]["external_apis"]
    inventory = next(f for f in http if f["client"] == "web_client")

    assert inventory["url"] == "/stock/{sku}"
    assert inventory["base_url"] == "http://inventory-service"
    assert inventory["response_type"] == "StockLevel"


def test_messaging_topology(model):
    destinations = model.architecture["integrations"]["messaging"]["destinations"]
    kafka = next(d for d in destinations if d["technology"] == "kafka")

    assert kafka == {"technology": "kafka", "destination": "orders.created.v1",
                     "producers": ["OrderServiceImpl.create"], "consumers": ["OrderEventsListener.onOrderCreated"]}


def test_storage_and_scheduling(model):
    integrations = model.architecture["integrations"]

    assert integrations["storage"][0]["bucket"] == "shop-invoices"
    assert integrations["scheduled_jobs"][0]["schedule"] == {"cron": "0 */5 * * * *"}


# ============================
# APPLICATION MODEL
# ============================

def test_data_model(model):
    data = model.architecture["data_model"]
    order = next(e for e in data["entities"] if e["name"] == "Order")

    assert order["table"] == "orders" and not order["table_inferred"]
    assert order["relationships"] == [{"field": "lines", "type": "OneToMany", "target": "OrderLine",
                                       "mapped_by": "order", "cascade": "ALL"}]
    email = next(f for f in order["fields"] if f["name"] == "customerEmail")
    assert email == {"name": "customerEmail", "type": "String", "column": "customer_email", "nullable": False}

    line = next(e for e in data["entities"] if e["name"] == "OrderLine")
    assert line["table"] == "order_line" and line["table_inferred"]

    repo = data["repositories"][0]
    assert (repo["entity"], repo["id_type"], repo["store"]) == ("Order", "Long", "jpa")
    assert repo["custom_queries"][0]["operation"] == "WRITE"

    tables = {t["name"]: t for t in data["tables"]}
    assert tables["audit_log"]["operations"] == ["WRITE"]
    assert tables["audit_log"]["accessed_by"] == ["AuditDao.record"]


def test_components_and_dependencies(model):
    components = {c["name"]: c for c in model.architecture["components"]}

    assert components["OrderController"]["stereotype"] == "rest_controller"
    assert components["OrderRepository"]["stereotype"] == "repository"
    assert components["OrderServiceImpl"]["description"] == "Order lifecycle: persistence, payment, events."

    deps = {d["field"]: d for d in components["OrderServiceImpl"]["dependencies"]}
    assert deps["kafkaTemplate"]["client"] == "kafka"
    assert deps["orderRepository"]["target_stereotype"] == "repository"
    assert components["OrderServiceImpl"]["config_properties"] == ["${topics.orders}"]

    # controller depends on the interface; the implementation is listed
    service_dep = components["OrderController"]["dependencies"][0]
    assert service_dep["implementations"] == ["com.shop.service.OrderServiceImpl"]


def test_module_build_and_runtime(model):
    module = model.architecture["modules"][0]

    assert (module["artifact"], module["spring_boot_version"], module["java_version"]) == ("shop-service", "3.3.4", "21")
    assert "messaging: Kafka" in module["technologies"]
    assert all(d["scope"] != "test" for d in module["dependencies"])

    runtime = module["runtime"]
    assert runtime["datasources"][0]["host"] == "db.internal"
    assert runtime["redis"] == {"host": "redis.internal", "port": 6379}
    assert module["properties"]["spring.datasource.password"] == "****"


def test_architecture_graph(model):
    graph = model.architecture["graph"]
    edges = {(e["from"], e["to"], e["kind"]) for e in graph["edges"]}

    service = "component:com.shop.service.OrderServiceImpl"
    assert (service, "datastore:postgresql:db.internal/shop", "write") in edges
    assert (service, "broker:kafka", "publishes") in edges
    assert ("component:com.shop.client.PaymentClient", "external:api.payments.example.com", "http") in edges
    assert ("broker:kafka", "component:com.shop.messaging.OrderEventsListener", "delivers") in edges
    assert ("component:com.shop.web.OrderController", service, "injects") in edges


# ============================
# CHANGE DETECTION
# ============================

def git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def shop_repo(tmp_path):
    repo = tmp_path / "shop"
    shutil.copytree(FIXTURE, repo)
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "test")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "init")
    return repo


def edit(path, old, new):
    text = path.read_text()
    assert old in text
    path.write_text(text.replace(old, new))


def test_service_change_impacts_only_reaching_endpoints(shop_repo):
    edit(shop_repo / "src/main/java/com/shop/client/PaymentClient.java", "/v1/charges/{id}", "/v2/charges/{id}")
    git(shop_repo, "commit", "-qam", "payments v2")

    routes = SpringExtractor().process_repository(str(shop_repo), ["src/main/java/com/shop/client/PaymentClient.java"], "HEAD")

    assert [r["handler"] for r in routes] == ["OrderController.create"]
    assert routes[0]["change_reasons"] == ["changed: PaymentClient.charge"]


def test_dto_change_reports_breaking_changes(shop_repo):
    dto = shop_repo / "src/main/java/com/shop/dto/LineItem.java"
    edit(dto, "@Min(1)", "@Min(5)")
    edit(dto, "    private int quantity;", "    private int quantity;\n    @NotNull\n    private String warehouse;")
    git(shop_repo, "commit", "-qam", "line item rules")

    routes = SpringExtractor().process_repository(str(shop_repo), ["src/main/java/com/shop/dto/LineItem.java"], "HEAD")

    assert [r["handler"] for r in routes] == ["OrderController.create"]
    assert routes[0]["change_reasons"] == ["changed model: LineItem"]
    assert routes[0]["breaking_changes"] == [
        {"type": "VALIDATION_CHANGED", "field": "quantity", "rule": "minimum", "old": 1, "new": 5, "dto": "LineItem"},
        {"type": "FIELD_ADDED", "field": "warehouse", "new_type": "String", "required": True, "dto": "LineItem"},
    ]


def test_entity_change_impacts_repository_users(shop_repo):
    edit(shop_repo / "src/main/java/com/shop/model/Order.java", "private OrderStatus status;", "private OrderStatus status;\n    private String note;")
    git(shop_repo, "commit", "-qam", "order note")

    routes = SpringExtractor().process_repository(str(shop_repo), ["src/main/java/com/shop/model/Order.java"], "HEAD")

    assert sorted(r["handler"] for r in routes) == ["OrderController.create", "OrderController.get"]


# ============================
# HELPERS
# ============================

@pytest.mark.parametrize("sql, expected", [
    ("SELECT o.id FROM orders o JOIN order_line l ON l.order_id = o.id", ("READ", ["orders", "order_line"])),
    ("insert into audit_log (a) values (?)", ("WRITE", ["audit_log"])),
    ("DELETE FROM sessions WHERE expired = true", ("DELETE", ["sessions"])),
    ("select o from Owner o join o.pets p", ("READ", ["Owner"])),
    ("FROM PetType ptype WHERE ptype.id = :id", ("READ", ["PetType"])),
])
def test_parse_sql(sql, expected):
    assert parse_sql(sql) == expected


def test_library_classification_uses_name_tokens():
    assert classify_library("org.springframework.security.oauth", "spring-security-oauth2") == ("security", "OAuth2")
    assert classify_library("com.h2database", "h2") == ("database", "H2")
    assert classify_library("org.springframework.kafka", "spring-kafka") == ("messaging", "Kafka")


# ============================
# MYBATIS & ENUM CONSTANTS
# ============================

MYBATIS_FILES = {
    "src/main/java/com/demo/ProductMapper.java": """
package com.demo;
public interface ProductMapper {
    Product selectByPrimaryKey(Long id);
    int updateStock(Long id, int delta);
}
""",
    "src/main/java/com/demo/AuditMapper.java": """
package com.demo;
@Mapper
public interface AuditMapper {
    @Insert("INSERT INTO audit_events (product_id) VALUES (#{id})")
    void record(Long id);
}
""",
    "src/main/resources/mapper/ProductMapper.xml": """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE mapper PUBLIC "-//mybatis.org//DTD Mapper 3.0//EN" "http://mybatis.org/dtd/mybatis-3-mapper.dtd">
<mapper namespace="com.demo.ProductMapper">
  <resultMap id="BaseResultMap" type="com.demo.Product"/>
  <sql id="cols">id, name, stock</sql>
  <select id="selectByPrimaryKey" resultMap="BaseResultMap">
    select <include refid="cols"/> from pms_product where id = #{id}
  </select>
  <update id="updateStock">
    update pms_product <set><if test="delta != null">stock = stock + #{delta}</if></set> where id = #{id}
  </update>
</mapper>
""",
    "src/main/java/com/demo/Queues.java": """
package com.demo;
public enum Queues {
    STOCK_CHANGED("stock.exchange", "stock.changed");
    private final String exchange;
    private final String routeKey;
    Queues(String exchange, String routeKey) { this.exchange = exchange; this.routeKey = routeKey; }
    public String getExchange() { return exchange; }
    public String getRouteKey() { return routeKey; }
}
""",
    "src/main/java/com/demo/StockController.java": """
package com.demo;
@RestController
public class StockController {
    @Autowired private ProductMapper productMapper;
    @Autowired private AuditMapper auditMapper;
    @Autowired private AmqpTemplate amqpTemplate;

    @PostMapping("/products/{id}/stock")
    public Product adjust(@PathVariable Long id, @RequestParam int delta) {
        productMapper.updateStock(id, delta);
        auditMapper.record(id);
        amqpTemplate.convertAndSend(Queues.STOCK_CHANGED.getExchange(), Queues.STOCK_CHANGED.getRouteKey(), id);
        return productMapper.selectByPrimaryKey(id);
    }
}
""",
}


def test_mybatis_mappers_and_enum_destinations(tmp_path):
    for rel, content in MYBATIS_FILES.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    extractor = SpringExtractor()
    extractor.build_model(str(tmp_path))
    adjust = extractor.endpoints[0]

    db = {(f["call"], f["operation"], tuple(f["table"])) for f in facts(adjust, "databases")}
    assert db == {
        ("ProductMapper.updateStock", "WRITE", ("pms_product",)),
        ("ProductMapper.selectByPrimaryKey", "READ", ("pms_product",)),
        ("AuditMapper.record", "WRITE", ("audit_events",)),
    }

    mapper = next(r for r in extractor.architecture["data_model"]["repositories"] if r["name"] == "ProductMapper")
    assert mapper["store"] == "mybatis" and mapper["entity"] == "Product"
    assert mapper["mapper_xml"] == "src/main/resources/mapper/ProductMapper.xml"
    select = next(q for q in mapper["custom_queries"] if q["method"] == "selectByPrimaryKey")
    assert select["query"] == "select id, name, stock from pms_product where id = #{id}"

    rabbit = facts(adjust, "messaging")[0]
    assert (rabbit["exchange"], rabbit["routing_key"]) == ("stock.exchange", "stock.changed")
