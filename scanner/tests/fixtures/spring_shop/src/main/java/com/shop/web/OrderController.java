package com.shop.web;

@RestController
@RequestMapping("/api/orders")
@RequiredArgsConstructor
public class OrderController {

    private final OrderService orderService;

    /**
     * Places a new order.
     */
    @PostMapping(consumes = "application/json")
    @ResponseStatus(HttpStatus.CREATED)
    public OrderResponse create(@Valid @RequestBody CreateOrderRequest request,
                                @RequestHeader("X-Request-Id") String requestId) {
        return orderService.create(request);
    }

    @GetMapping("/{id}")
    @PreAuthorize("hasRole('USER')")
    public ResponseEntity<OrderResponse> get(@PathVariable Long id) {
        if (id < 0) {
            return ResponseEntity.badRequest().build();
        }
        return ResponseEntity.ok(orderService.get(id));
    }

    @DeleteMapping("/{id}")
    public void cancel(@PathVariable("id") Long orderId) {
        throw new ResponseStatusException(HttpStatus.GONE, "cancelled orders are archived");
    }

    @GetMapping
    public Page<OrderResponse> list(Pageable pageable, @RequestParam(required = false) OrderStatus status) {
        return Page.empty();
    }
}
