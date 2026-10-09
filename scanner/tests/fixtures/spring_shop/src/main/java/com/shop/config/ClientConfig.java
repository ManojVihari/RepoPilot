package com.shop.config;

@Configuration
public class ClientConfig {
    @Value("${inventory.url}")
    private String inventoryUrl;

    @Bean
    public WebClient inventoryWebClient(WebClient.Builder builder) {
        return builder.baseUrl(inventoryUrl).build();
    }

    @Bean
    public RestTemplate restTemplate() { return new RestTemplate(); }
}
