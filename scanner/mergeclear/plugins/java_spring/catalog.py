"""
Knowledge tables used to classify libraries and Java types.
"""
import re

# (artifact substring, category, technology) - first match wins, so put
# specific entries before generic ones.
LIBRARY_CATALOG = [
    # databases
    ("spring-boot-starter-data-jpa", "database", "JPA / Hibernate"),
    ("spring-boot-starter-data-jdbc", "database", "Spring Data JDBC"),
    ("spring-boot-starter-jdbc", "database", "JDBC"),
    ("spring-boot-starter-data-r2dbc", "database", "R2DBC"),
    ("spring-boot-starter-data-mongodb", "database", "MongoDB"),
    ("spring-boot-starter-data-cassandra", "database", "Cassandra"),
    ("spring-boot-starter-data-neo4j", "database", "Neo4j"),
    ("spring-boot-starter-data-elasticsearch", "search", "Elasticsearch"),
    ("spring-boot-starter-data-couchbase", "database", "Couchbase"),
    ("postgresql", "database", "PostgreSQL"),
    ("r2dbc-postgresql", "database", "PostgreSQL"),
    ("mysql-connector", "database", "MySQL"),
    ("mariadb", "database", "MariaDB"),
    ("ojdbc", "database", "Oracle"),
    ("mssql-jdbc", "database", "SQL Server"),
    ("h2", "database", "H2"),
    ("hsqldb", "database", "HSQLDB"),
    ("derby", "database", "Derby"),
    ("sqlite-jdbc", "database", "SQLite"),
    ("mongodb-driver", "database", "MongoDB"),
    ("dynamodb", "database", "DynamoDB"),
    ("hibernate-core", "database", "Hibernate"),
    ("mybatis", "database", "MyBatis"),
    ("jooq", "database", "jOOQ"),
    ("flyway", "db-migration", "Flyway"),
    ("liquibase", "db-migration", "Liquibase"),
    ("elasticsearch", "search", "Elasticsearch"),
    ("opensearch", "search", "OpenSearch"),
    ("solr", "search", "Solr"),
    # caches
    ("spring-boot-starter-data-redis", "cache", "Redis"),
    ("redisson", "cache", "Redis (Redisson)"),
    ("jedis", "cache", "Redis (Jedis)"),
    ("lettuce", "cache", "Redis (Lettuce)"),
    ("spring-boot-starter-cache", "cache", "Spring Cache"),
    ("caffeine", "cache", "Caffeine"),
    ("ehcache", "cache", "Ehcache"),
    ("hazelcast", "cache", "Hazelcast"),
    ("memcached", "cache", "Memcached"),
    # messaging
    ("spring-kafka", "messaging", "Kafka"),
    ("kafka-clients", "messaging", "Kafka"),
    ("spring-cloud-stream-binder-kafka", "messaging", "Kafka (Spring Cloud Stream)"),
    ("spring-cloud-stream-binder-rabbit", "messaging", "RabbitMQ (Spring Cloud Stream)"),
    ("spring-cloud-stream", "messaging", "Spring Cloud Stream"),
    ("spring-boot-starter-amqp", "messaging", "RabbitMQ"),
    ("spring-rabbit", "messaging", "RabbitMQ"),
    ("activemq", "messaging", "ActiveMQ"),
    ("artemis", "messaging", "ActiveMQ Artemis"),
    ("spring-jms", "messaging", "JMS"),
    ("spring-cloud-aws-messaging", "messaging", "AWS SQS/SNS"),
    ("spring-cloud-aws-starter-sqs", "messaging", "AWS SQS"),
    ("sqs", "messaging", "AWS SQS"),
    ("sns", "messaging", "AWS SNS"),
    ("pulsar", "messaging", "Pulsar"),
    ("google-cloud-pubsub", "messaging", "Google Pub/Sub"),
    ("azure-servicebus", "messaging", "Azure Service Bus"),
    ("nats", "messaging", "NATS"),
    # http / rpc clients
    ("spring-cloud-starter-openfeign", "http-client", "OpenFeign"),
    ("feign", "http-client", "Feign"),
    ("spring-boot-starter-webflux", "web", "Spring WebFlux / WebClient"),
    ("okhttp", "http-client", "OkHttp"),
    ("httpclient5", "http-client", "Apache HttpClient 5"),
    ("httpclient", "http-client", "Apache HttpClient"),
    ("retrofit", "http-client", "Retrofit"),
    ("grpc", "rpc", "gRPC"),
    ("graphql", "api", "GraphQL"),
    ("spring-ws", "api", "SOAP (Spring WS)"),
    ("cxf", "api", "SOAP (CXF)"),
    # cloud / infra
    ("spring-cloud-starter-netflix-eureka-server", "service-discovery", "Eureka Server"),
    ("spring-cloud-starter-netflix-eureka-client", "service-discovery", "Eureka Client"),
    ("spring-cloud-starter-consul", "service-discovery", "Consul"),
    ("spring-cloud-kubernetes", "service-discovery", "Kubernetes"),
    ("spring-cloud-config-server", "configuration", "Spring Cloud Config Server"),
    ("spring-cloud-starter-config", "configuration", "Spring Cloud Config Client"),
    ("spring-cloud-config-client", "configuration", "Spring Cloud Config Client"),
    ("spring-cloud-starter-vault", "configuration", "Vault"),
    ("spring-cloud-starter-gateway", "api-gateway", "Spring Cloud Gateway"),
    ("zuul", "api-gateway", "Netflix Zuul"),
    ("spring-cloud-starter-loadbalancer", "load-balancing", "Spring Cloud LoadBalancer"),
    ("ribbon", "load-balancing", "Ribbon"),
    ("resilience4j", "resilience", "Resilience4j"),
    ("hystrix", "resilience", "Hystrix"),
    ("spring-cloud-starter-circuitbreaker", "resilience", "Spring Cloud Circuit Breaker"),
    ("spring-boot-admin", "observability", "Spring Boot Admin"),
    ("spring-boot-starter-actuator", "observability", "Actuator"),
    ("micrometer-registry-prometheus", "observability", "Prometheus"),
    ("micrometer-tracing", "observability", "Micrometer Tracing"),
    ("zipkin", "observability", "Zipkin"),
    ("sleuth", "observability", "Sleuth"),
    ("opentelemetry", "observability", "OpenTelemetry"),
    ("logstash", "observability", "Logstash"),
    ("aws-java-sdk-s3", "storage", "AWS S3"),
    ("software.amazon.awssdk:s3", "storage", "AWS S3"),
    ("google-cloud-storage", "storage", "Google Cloud Storage"),
    ("azure-storage", "storage", "Azure Storage"),
    ("minio", "storage", "MinIO"),
    ("spring-boot-starter-mail", "email", "JavaMail"),
    ("sendgrid", "email", "SendGrid"),
    ("twilio", "notification", "Twilio"),
    ("stripe", "payment", "Stripe"),
    ("quartz", "scheduling", "Quartz"),
    ("spring-boot-starter-batch", "batch", "Spring Batch"),
    ("shedlock", "scheduling", "ShedLock"),
    # security
    ("spring-boot-starter-oauth2-resource-server", "security", "OAuth2 Resource Server"),
    ("spring-boot-starter-oauth2-client", "security", "OAuth2 Client"),
    ("spring-security-oauth2", "security", "OAuth2"),
    ("spring-cloud-starter-oauth2", "security", "OAuth2"),
    ("spring-boot-starter-security", "security", "Spring Security"),
    ("keycloak", "security", "Keycloak"),
    ("jjwt", "security", "JWT (jjwt)"),
    ("java-jwt", "security", "JWT"),
    ("nimbus-jose-jwt", "security", "JWT (Nimbus)"),
    # web / api docs / misc
    ("spring-boot-starter-web", "web", "Spring MVC"),
    ("spring-boot-starter-thymeleaf", "web-ui", "Thymeleaf"),
    ("spring-boot-starter-validation", "validation", "Bean Validation"),
    ("springdoc-openapi", "api-docs", "OpenAPI (springdoc)"),
    ("springfox", "api-docs", "Swagger (springfox)"),
    ("swagger", "api-docs", "Swagger"),
    ("lombok", "code-generation", "Lombok"),
    ("mapstruct", "code-generation", "MapStruct"),
    ("spring-boot-starter-test", "test", "Spring Boot Test"),
    ("junit", "test", "JUnit"),
    ("mockito", "test", "Mockito"),
    ("testcontainers", "test", "Testcontainers"),
]


def classify_library(group: str, artifact: str):
    """(category, technology) for a dependency, or (None, None)."""
    key = f"{group}:{artifact}".lower()
    tokens = re.split(r"[-.:_]", artifact.lower())

    for needle, category, tech in LIBRARY_CATALOG:
        if any(sep in needle for sep in "-:."):
            matched = needle in key
        else:
            # single word: match a whole name token ("h2" must not match "oauth2")
            matched = any(token.startswith(needle) for token in tokens)

        if matched:
            return category, tech

    return None, None


# Spring Data repository base interfaces -> store kind
REPOSITORY_BASES = {
    "JpaRepository": "jpa",
    "JpaSpecificationExecutor": "jpa",
    "QuerydslPredicateExecutor": "jpa",
    "RevisionRepository": "jpa",
    "MongoRepository": "mongodb",
    "ReactiveMongoRepository": "mongodb",
    "R2dbcRepository": "r2dbc",
    "ElasticsearchRepository": "elasticsearch",
    "CassandraRepository": "cassandra",
    "ReactiveCassandraRepository": "cassandra",
    "Neo4jRepository": "neo4j",
    "ReactiveNeo4jRepository": "neo4j",
    "CouchbaseRepository": "couchbase",
    "KeyValueRepository": "key-value",
    "CrudRepository": None,
    "ListCrudRepository": None,
    "PagingAndSortingRepository": None,
    "ListPagingAndSortingRepository": None,
    "ReactiveCrudRepository": None,
    "ReactiveSortingRepository": None,
    "Repository": None,
}

# Receiver types of interest -> integration kind
CLIENT_TYPES = {
    # http
    "RestTemplate": "rest_template",
    "TestRestTemplate": "rest_template",
    "OAuth2RestTemplate": "rest_template",
    "OAuth2RestOperations": "rest_template",
    "RestOperations": "rest_template",
    "WebClient": "web_client",
    "RestClient": "rest_client",
    "HttpClient": "java_http_client",
    "HttpRequest": "java_http_client",
    "OkHttpClient": "okhttp",
    "CloseableHttpClient": "apache_http_client",
    # databases
    "JdbcTemplate": "jdbc",
    "NamedParameterJdbcTemplate": "jdbc",
    "JdbcClient": "jdbc",
    "JdbcOperations": "jdbc",
    "EntityManager": "jpa_entity_manager",
    "Session": "hibernate_session",
    "MongoTemplate": "mongo_template",
    "MongoOperations": "mongo_template",
    "ReactiveMongoTemplate": "mongo_template",
    "R2dbcEntityTemplate": "r2dbc",
    "DatabaseClient": "r2dbc",
    "DynamoDbClient": "dynamodb",
    "DynamoDbEnhancedClient": "dynamodb",
    "DynamoDBMapper": "dynamodb",
    "ElasticsearchOperations": "elasticsearch",
    "ElasticsearchClient": "elasticsearch",
    "RestHighLevelClient": "elasticsearch",
    # caches
    "RedisTemplate": "redis",
    "StringRedisTemplate": "redis",
    "ReactiveRedisTemplate": "redis",
    "ReactiveStringRedisTemplate": "redis",
    "RedissonClient": "redis",
    "Jedis": "redis",
    "JedisPool": "redis",
    "RedisCommands": "redis",
    "CacheManager": "cache_manager",
    "Cache": "local_cache",
    "LoadingCache": "local_cache",
    "HazelcastInstance": "hazelcast",
    # messaging
    "KafkaTemplate": "kafka",
    "ReactiveKafkaProducerTemplate": "kafka",
    "KafkaProducer": "kafka",
    "Producer": "kafka",
    "RabbitTemplate": "rabbitmq",
    "AmqpTemplate": "rabbitmq",
    "RabbitMessagingTemplate": "rabbitmq",
    "JmsTemplate": "jms",
    "JmsMessagingTemplate": "jms",
    "StreamBridge": "stream",
    "SqsTemplate": "sqs",
    "SqsClient": "sqs",
    "SqsAsyncClient": "sqs",
    "AmazonSQS": "sqs",
    "AmazonSQSAsync": "sqs",
    "QueueMessagingTemplate": "sqs",
    "SnsClient": "sns",
    "AmazonSNS": "sns",
    "NotificationMessagingTemplate": "sns",
    "PubSubTemplate": "pubsub",
    "ApplicationEventPublisher": "event",
    "SimpMessagingTemplate": "websocket",
    # other
    "JavaMailSender": "email",
    "MailSender": "email",
    "S3Client": "s3",
    "S3AsyncClient": "s3",
    "AmazonS3": "s3",
    "S3Template": "s3",
    "Storage": "gcs",
    "BlobServiceClient": "azure_blob",
    "MinioClient": "s3",
}

HTTP_METHOD_BY_REST_TEMPLATE_CALL = {
    "getForObject": "GET",
    "getForEntity": "GET",
    "postForObject": "POST",
    "postForEntity": "POST",
    "postForLocation": "POST",
    "put": "PUT",
    "patchForObject": "PATCH",
    "delete": "DELETE",
    "headForHeaders": "HEAD",
    "optionsForAllow": "OPTIONS",
}

HTTP_VERBS = {"get", "post", "put", "patch", "delete", "head", "options"}

HTTP_STATUS = {
    "CONTINUE": 100, "SWITCHING_PROTOCOLS": 101,
    "OK": 200, "CREATED": 201, "ACCEPTED": 202, "NON_AUTHORITATIVE_INFORMATION": 203,
    "NO_CONTENT": 204, "RESET_CONTENT": 205, "PARTIAL_CONTENT": 206,
    "MULTIPLE_CHOICES": 300, "MOVED_PERMANENTLY": 301, "FOUND": 302, "SEE_OTHER": 303,
    "NOT_MODIFIED": 304, "TEMPORARY_REDIRECT": 307, "PERMANENT_REDIRECT": 308,
    "BAD_REQUEST": 400, "UNAUTHORIZED": 401, "PAYMENT_REQUIRED": 402, "FORBIDDEN": 403,
    "NOT_FOUND": 404, "METHOD_NOT_ALLOWED": 405, "NOT_ACCEPTABLE": 406,
    "REQUEST_TIMEOUT": 408, "CONFLICT": 409, "GONE": 410, "LENGTH_REQUIRED": 411,
    "PRECONDITION_FAILED": 412, "PAYLOAD_TOO_LARGE": 413, "UNSUPPORTED_MEDIA_TYPE": 415,
    "UNPROCESSABLE_ENTITY": 422, "UNPROCESSABLE_CONTENT": 422, "LOCKED": 423,
    "TOO_MANY_REQUESTS": 429,
    "INTERNAL_SERVER_ERROR": 500, "NOT_IMPLEMENTED": 501, "BAD_GATEWAY": 502,
    "SERVICE_UNAVAILABLE": 503, "GATEWAY_TIMEOUT": 504,
}

STATUS_NAME = {code: name for name, code in reversed(list(HTTP_STATUS.items()))}

# ResponseEntity.<builder>() -> status
RESPONSE_ENTITY_BUILDERS = {
    "ok": 200, "created": 201, "accepted": 202, "noContent": 204,
    "badRequest": 400, "notFound": 404, "unprocessableEntity": 422, "internalServerError": 500,
}

# Spring-provided exceptions with a well-known status
KNOWN_EXCEPTION_STATUS = {
    "MethodArgumentNotValidException": 400,
    "ConstraintViolationException": 400,
    "HttpMessageNotReadableException": 400,
    "MissingServletRequestParameterException": 400,
    "MethodArgumentTypeMismatchException": 400,
    "AccessDeniedException": 403,
    "AuthenticationException": 401,
    "BadCredentialsException": 401,
    "UsernameNotFoundException": 401,
    "EntityNotFoundException": 404,
    "EmptyResultDataAccessException": 404,
    "NoSuchElementException": 404,
    "DataIntegrityViolationException": 409,
    "OptimisticLockingFailureException": 409,
    "HttpRequestMethodNotSupportedException": 405,
    "HttpMediaTypeNotSupportedException": 415,
}

STEREOTYPES = [
    # (annotation, stereotype, layer)
    ("RestController", "rest_controller", "web"),
    ("Controller", "controller", "web"),
    ("RestControllerAdvice", "controller_advice", "web"),
    ("ControllerAdvice", "controller_advice", "web"),
    ("FeignClient", "feign_client", "integration"),
    ("Repository", "repository", "data"),
    ("Service", "service", "service"),
    ("Configuration", "configuration", "config"),
    ("SpringBootApplication", "application", "config"),
    ("ConfigurationProperties", "configuration_properties", "config"),
    ("Entity", "entity", "domain"),
    ("Document", "document", "domain"),
    ("Table", "entity", "domain"),
    ("Embeddable", "embeddable", "domain"),
    ("MappedSuperclass", "entity", "domain"),
    ("RedisHash", "entity", "domain"),
    ("Mapper", "mapper", "service"),
    ("Component", "component", "service"),
]

INJECTION_ANNOTATIONS = {"Autowired", "Inject", "Resource", "Value"}

# Framework parameter types that are not part of the HTTP contract
FRAMEWORK_PARAM_TYPES = {
    "HttpServletRequest", "HttpServletResponse", "ServletRequest", "ServletResponse",
    "HttpSession", "WebRequest", "NativeWebRequest", "ServerWebExchange", "ServerHttpRequest",
    "ServerHttpResponse", "Model", "ModelMap", "ModelAndView", "BindingResult", "Errors",
    "RedirectAttributes", "SessionStatus", "Locale", "TimeZone", "ZoneId", "Principal",
    "Authentication", "UriComponentsBuilder", "HttpHeaders", "HttpEntity", "InputStream",
    "OutputStream", "Reader", "Writer", "SseEmitter", "Jwt", "OAuth2User", "OidcUser",
}

SIMPLE_TYPES = {
    "String", "CharSequence", "Integer", "Long", "Short", "Byte", "Double", "Float",
    "Boolean", "Character", "BigDecimal", "BigInteger", "Number", "UUID", "Date",
    "LocalDate", "LocalDateTime", "LocalTime", "Instant", "OffsetDateTime",
    "ZonedDateTime", "Duration", "Period", "YearMonth", "Year", "Object", "URI", "URL",
    "byte", "short", "int", "long", "float", "double", "boolean", "char",
    "MultipartFile", "Part", "Resource", "byte[]",
}

COLLECTION_TYPES = {
    "List", "Set", "Collection", "Iterable", "SortedSet", "LinkedList", "ArrayList",
    "HashSet", "LinkedHashSet", "TreeSet", "Queue", "Deque", "Stream", "Flux", "Page",
    "Slice",
}

MAP_TYPES = {"Map", "HashMap", "LinkedHashMap", "TreeMap", "SortedMap", "ConcurrentMap", "MultiValueMap"}

WRAPPER_TYPES = {
    "Optional", "ResponseEntity", "Mono", "CompletableFuture", "Future", "Callable",
    "DeferredResult", "HttpEntity", "EntityModel", "CompletionStage", "ListenableFuture",
}

VALIDATION_ANNOTATIONS = {
    "NotNull", "NotEmpty", "NotBlank", "Null", "Size", "Min", "Max", "DecimalMin",
    "DecimalMax", "Digits", "Email", "Pattern", "Positive", "PositiveOrZero",
    "Negative", "NegativeOrZero", "Past", "PastOrPresent", "Future", "FutureOrPresent",
    "AssertTrue", "AssertFalse", "Length", "Range", "URL", "Valid",
}
