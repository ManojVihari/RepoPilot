# DocAI scanner

Static analysis for API documentation and architecture discovery. Runs in CI,
parses the repository with tree-sitter (no compilation, no running the app)
and sends the result to the DocAI server.

```bash
pip install -e scanner
docai-scan --repo . --commit "$GIT_COMMIT" --server https://docai.internal   # or omit --server to print JSON
docai-scan --repo . --commit HEAD -v                                         # debug logs on stderr
```

The scanner diffs `<commit>^..<commit>`. When the parent is not available
(first commit, shallow clone) every tracked file counts as changed.

## Output

```jsonc
{
  "scanner_version": "2.0",
  "repository": "shop",
  "commit": "abc123",
  "frameworks": ["spring"],
  "routes": [ /* endpoints impacted by the commit, see below */ ],
  "architecture": { "spring": { /* application model, see below */ } }
}
```

### `routes[]` - impacted endpoints (Spring)

Only endpoints affected by the commit are listed. Impact is precise: git diff
hunks are mapped to the methods they touch, then followed backwards through
the resolved call graph (interface calls dispatch to their implementations,
`@EventListener`s are linked to `publishEvent`). Changes to DTOs, entities and
repositories impact every endpoint that uses them.

| Field | Content |
|---|---|
| `function`, `method`, `path`, `paths`, `context_path` | Handler name, HTTP method (`ANY` for `@RequestMapping` without method), path(s), `server.servlet.context-path` |
| `handler`, `controller`, `module`, `file`, `line` | Where the endpoint lives |
| `summary`, `description`, `deprecated` | `@Operation`/`@ApiOperation` and Javadoc |
| `params[]` | `name`, `type`, `in` (`path`/`query`/`header`/`cookie`/`body`/`model`/`multipart`), `required`, `default`, `validation`, `schema` (DTO fields, nested) |
| `request_body` | `type`, `required`, `validated` (`@Valid`), `schema` |
| `response` | `type` (declared), `body_type` (unwrapped from `ResponseEntity`/`Optional`/`Mono`...), `schema`, `view` for MVC pages |
| `consumes`, `produces` | From the mapping annotation |
| `status_codes[]` | `{code, reason, source}` from `@ResponseStatus`, `ResponseEntity` builders, `ResponseStatusException`, `@ExceptionHandler` mappings, bean validation |
| `exceptions[]` | Exceptions thrown anywhere in the call chain with the status they map to (`mapped_by`: handler, `@ResponseStatus`, Spring default or `unhandled` → 500) |
| `security` | Method annotations (`@PreAuthorize`, `@Secured`, `@RolesAllowed`) and the first matching URL rule of the `SecurityFilterChain` / `HttpSecurity` config |
| `integrations` | Everything reached through the call chain, grouped: `databases`, `caches`, `messaging`, `external_apis`, `events`, `storage`, `email`, `search`, `transactions`, `resilience`, `async`. Each fact names its `source` method |
| `call_graph` | `direct` and `full` (resolved `Class.method`), `tree` (nested, depth 6) |
| `change_reasons` | Why the endpoint is impacted (`changed: OrderService.create`, `changed model: LineItem`, ...) |
| `breaking_changes[]` | Contract changes of changed DTOs: `FIELD_ADDED` (with `required`), `FIELD_REMOVED`, `TYPE_CHANGED`, `VALIDATION_ADDED/REMOVED/CHANGED` |
| `errors`, `calls`, `db_ops`, `impact`, `referenced_types` | Compact forms kept for existing consumers |

Integration facts, by kind:

| Kind | Detected from | Key fields |
|---|---|---|
| `database` | Spring Data repositories (JPA, Mongo, R2DBC, Elasticsearch, Cassandra, Neo4j...), `@Query`, MyBatis mappers (XML and `@Select`...), `JdbcTemplate`/`JdbcClient`, `EntityManager`, `MongoTemplate` | `technology`, `operation` (READ/WRITE/DELETE), `table[]`, `entity`, `query`, `call` |
| `cache` | `@Cacheable`/`@CachePut`/`@CacheEvict`/`@Caching`, `RedisTemplate` & co, `CacheManager`, Caffeine | `operation`, `cache_names`, `key`, `command` |
| `messaging` | `KafkaTemplate`, `RabbitTemplate`/`AmqpTemplate`, `JmsTemplate`, `StreamBridge` (+ bindings from config), SQS, SNS, Pub/Sub, Redis pub/sub, `@SendTo`, functional stream beans | `direction`, `technology`, `destination`, `exchange`/`routing_key`, `payload_type` |
| `http` | `RestTemplate`, `WebClient`, `RestClient` (base URL from `@Bean` builders), Feign clients, `java.net.http`, OkHttp, Apache HttpClient, `DiscoveryClient` lookups | `client`, `http_method`, `url`, `base_url`, `host`, `target_service`/`target_module` (when it is another module of the repo) |
| `event` | `ApplicationEventPublisher.publishEvent` | `event_type` (listeners are linked into the call graph) |
| `storage`, `email`, `search` | S3/GCS/Azure clients, `JavaMailSender`, Elasticsearch clients | `bucket`, `operation`, `call` |
| `transaction`, `resilience`, `async` | `@Transactional`, `@CircuitBreaker`/`@Retry`/..., `@Async` | `read_only`, `name`, `fallback` |

String values (URLs, topics, keys, SQL) are resolved through constants,
`@Value` fields, local variables, enum constants and the module configuration;
unresolved parts appear as `{name}` placeholders.

### `architecture.spring` - application model

| Key | Content |
|---|---|
| `summary` | Counts, Spring Boot apps, technology stack by category, `monolith`/`microservices` |
| `modules[]` | Maven/Gradle module: coordinates, Java/Spring Boot/Spring Cloud versions, classified `dependencies`, `technologies`, `runtime` (app name, port, context path, datasources, MongoDB, Redis, Kafka, RabbitMQ, discovery, config server, identity provider, gateway/Zuul routes, stream bindings, external URLs), `properties` and `profile_properties` (secrets masked), `main_class`, `client_base_urls` |
| `components[]` | Spring beans with `stereotype`/`layer`, annotations, Javadoc, injected `dependencies` (field/constructor/setter, interface → implementations), `config_properties`, public methods, `@Bean` definitions, integrations used |
| `data_model` | `entities` (table/collection, fields, ids, columns, relationships), `repositories` (store, entity, id type, custom queries with operation and tables, MyBatis mapper XML), `tables` (operations and accessing methods) |
| `endpoints[]` | Every endpoint (not only impacted ones), compact: method, path, request/response types, status codes, tables, caches, published destinations, external calls |
| `integrations` | App-wide facts: databases, caches, messaging (`producers`, `consumers`, `destinations` pairing producers with consumers), external APIs, Feign clients, events, scheduled jobs, storage, email, search, resilience |
| `security` | Technologies, mechanisms (JWT, OAuth2 resource server, form login...), CSRF, URL rules |
| `exception_handling` | Exception → status mappings (`@ExceptionHandler`, `@ResponseStatus`) |
| `graph` | `nodes` (modules, components, datastores, caches, brokers, external services, infrastructure) and `edges` (`contains`, `injects`, `calls`, `read`/`write`, `publishes`, `delivers`, `http`, `routes`, `connects`, `registers`, `reads_config`, `triggers`) ready to render |

Test sources (`src/test`, `src/it`) are excluded. Nothing is executed: values
only known at runtime (environment variables without defaults, values built
from method parameters) stay as placeholders.
