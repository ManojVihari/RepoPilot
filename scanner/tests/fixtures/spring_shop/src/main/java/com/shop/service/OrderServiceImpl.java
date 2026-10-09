package com.shop.service;

/** Order lifecycle: persistence, payment, events. */
@Service
@RequiredArgsConstructor
public class OrderServiceImpl implements OrderService {

    private final OrderRepository orderRepository;
    private final AuditDao auditDao;
    private final PaymentClient paymentClient;
    private final KafkaTemplate<String, OrderCreatedEvent> kafkaTemplate;
    private final ApplicationEventPublisher events;
    private final StringRedisTemplate redis;

    @Value("${topics.orders}")
    private String ordersTopic;

    @Override
    @Transactional
    @CacheEvict(cacheNames = "orders", allEntries = true)
    public OrderResponse create(CreateOrderRequest request) {
        Order order = orderRepository.save(new Order());
        auditDao.record(order.getId(), "CREATED");
        if (!paymentClient.charge(order.getId())) {
            throw new PaymentDeclinedException();
        }
        kafkaTemplate.send(ordersTopic, new OrderCreatedEvent(order.getId(), request.customerEmail()));
        events.publishEvent(new OrderCreatedEvent(order.getId(), request.customerEmail()));
        redis.opsForValue().set("order:" + order.getId(), "NEW");
        return new OrderResponse();
    }

    @Override
    @Cacheable("orders")
    public OrderResponse get(Long id) {
        orderRepository.findById(id).orElseThrow(() -> new OrderNotFoundException(id));
        return new OrderResponse();
    }
}
