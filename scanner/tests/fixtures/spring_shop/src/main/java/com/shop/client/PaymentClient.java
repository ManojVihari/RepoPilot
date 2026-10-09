package com.shop.client;

@Component
public class PaymentClient {
    private final RestTemplate restTemplate;

    @Value("${payments.base-url}")
    private String baseUrl;

    public PaymentClient(RestTemplate restTemplate) { this.restTemplate = restTemplate; }

    @CircuitBreaker(name = "payments", fallbackMethod = "chargeFallback")
    public boolean charge(Long orderId) {
        return restTemplate.postForObject(baseUrl + "/v1/charges/{id}", null, Boolean.class, orderId);
    }
}
