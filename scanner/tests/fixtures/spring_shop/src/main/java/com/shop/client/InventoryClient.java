package com.shop.client;

@Component
public class InventoryClient {
    private final WebClient inventoryWebClient;

    public InventoryClient(WebClient inventoryWebClient) { this.inventoryWebClient = inventoryWebClient; }

    public Mono<StockLevel> stock(String sku) {
        return inventoryWebClient.get().uri("/stock/{sku}", sku).retrieve().bodyToMono(StockLevel.class);
    }
}
