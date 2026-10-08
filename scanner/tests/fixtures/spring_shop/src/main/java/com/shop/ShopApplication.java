package com.shop;

@SpringBootApplication
@EnableCaching
@EnableScheduling
public class ShopApplication {
    public static void main(String[] args) { SpringApplication.run(ShopApplication.class, args); }
}
