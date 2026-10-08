package com.shop.messaging;

@Component
public class OrderEventsListener {
    private final S3Client s3;
    private final SqsTemplate sqs;

    @Value("${storage.bucket}")
    private String bucket;

    public OrderEventsListener(S3Client s3, SqsTemplate sqs) { this.s3 = s3; this.sqs = sqs; }

    @KafkaListener(topics = "${topics.orders}", groupId = "invoicing")
    public void onOrderCreated(OrderCreatedEvent event) {
        s3.putObject(PutObjectRequest.builder().bucket(bucket).key("invoice-" + event.orderId()).build(), RequestBody.empty());
    }

    @EventListener
    public void notifyWarehouse(OrderCreatedEvent event) {
        sqs.send("warehouse-picking", event);
    }

    @Scheduled(cron = "0 */5 * * * *")
    public void retryFailed() { }
}
