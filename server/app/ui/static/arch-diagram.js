/*
 * Layered architecture diagram.
 *
 * Boxes are plain HTML laid out in columns by CSS (so text never overlaps and
 * the page stays readable without the lines); connections are drawn on an SVG
 * layer behind them from the boxes' real positions. Hover or focus a box to
 * highlight its connections, click it for the full list in the details panel.
 *
 *   ArchDiagram.render(container, view, { onOpenModule: path => ... })
 *
 * view = { columns: [{title, nodes: [{id, label, type, type_label, meta, badges, group, muted}]}],
 *          links: [{source, target, kinds, category, details}] }
 */
(function () {
    "use strict";

    var SVG = "http://www.w3.org/2000/svg";
    var CATEGORIES = [
        ["call", "Calls"],
        ["library", "Libraries"],
        ["data", "Data"],
        ["messaging", "Messaging"],
        ["external", "External"],
    ];

    function el(tag, cls, text) {
        var node = document.createElement(tag);
        if (cls) node.className = cls;
        if (text != null) node.textContent = text;
        return node;
    }

    // long Java class names: allow line breaks between words of a camelCase name
    function nameNode(text) {
        var span = el("span", "ad-name");
        String(text).split(/(?=[A-Z][a-z])|(?<=[-_.@/])/).forEach(function (part, i) {
            if (i) span.appendChild(document.createElement("wbr"));
            span.appendChild(document.createTextNode(part));
        });
        return span;
    }

    function render(container, view, options) {
        options = options || {};
        var uid = Math.random().toString(36).slice(2, 8);
        container.innerHTML = "";
        if (!view || !view.columns || !view.columns.length) {
            container.appendChild(el("p", "empty small", "Nothing to draw for this selection."));
            return;
        }

        var nodesById = {}, columnOf = {}, linksOf = {};
        view.columns.forEach(function (col, i) {
            col.nodes.forEach(function (n) { nodesById[n.id] = n; columnOf[n.id] = i; linksOf[n.id] = []; });
        });
        var links = (view.links || []).filter(function (l) { return nodesById[l.source] && nodesById[l.target]; });
        links.forEach(function (l, i) { l._i = i; linksOf[l.source].push(l); linksOf[l.target].push(l); });

        var present = {};
        links.forEach(function (l) { present[l.category] = true; });

        var root = el("div", "ad");

        // legend that doubles as a filter
        var toolbar = el("div", "ad-toolbar");
        toolbar.setAttribute("role", "group");
        toolbar.setAttribute("aria-label", "Show connections");
        CATEGORIES.forEach(function (c) {
            if (!present[c[0]]) return;
            var b = el("button", "ad-toggle cat-" + c[0]);
            b.type = "button";
            b.setAttribute("aria-pressed", "true");
            b.appendChild(el("span", "ad-swatch"));
            b.appendChild(document.createTextNode(c[1]));
            b.addEventListener("click", function () {
                var on = b.getAttribute("aria-pressed") !== "true";
                b.setAttribute("aria-pressed", String(on));
                canvas.classList.toggle("hide-" + c[0], !on);
            });
            toolbar.appendChild(b);
        });
        var dense = links.length > 40;
        if (dense) {
            var all = el("button", "ad-toggle ad-all");
            all.type = "button";
            all.setAttribute("aria-pressed", "false");
            all.textContent = "Show all lines";
            all.addEventListener("click", function () {
                var on = all.getAttribute("aria-pressed") !== "true";
                all.setAttribute("aria-pressed", String(on));
                canvas.classList.toggle("ad-quiet", !on);
            });
            toolbar.appendChild(all);
        }
        toolbar.appendChild(el("span", "ad-hint", dense
            ? links.length + " connections: hover or select a box to see its own"
            : "Hover a box to trace it · click for details"));
        root.appendChild(toolbar);

        var canvas = el("div", "ad-canvas" + (links.length > 40 ? " ad-quiet" : ""));
        var svg = document.createElementNS(SVG, "svg");
        svg.setAttribute("class", "ad-lines");
        svg.setAttribute("aria-hidden", "true");
        var defs = document.createElementNS(SVG, "defs");
        CATEGORIES.forEach(function (c) {
            var marker = document.createElementNS(SVG, "marker");
            marker.setAttribute("id", "ad-arrow-" + c[0] + "-" + uid);
            marker.setAttribute("viewBox", "0 0 8 8");
            marker.setAttribute("refX", "7");
            marker.setAttribute("refY", "4");
            marker.setAttribute("markerWidth", "7");
            marker.setAttribute("markerHeight", "7");
            marker.setAttribute("orient", "auto");
            var tip = document.createElementNS(SVG, "path");
            tip.setAttribute("d", "M0,0 L8,4 L0,8 z");
            tip.setAttribute("class", "ad-tip cat-" + c[0]);
            marker.appendChild(tip);
            defs.appendChild(marker);
        });
        svg.appendChild(defs);
        canvas.appendChild(svg);

        var grid = el("div", "ad-columns");
        grid.style.setProperty("--ad-cols", view.columns.length);
        var boxes = {}, columnEls = [], columnBoxes = [];

        view.columns.forEach(function (col) {
            var column = el("section", "ad-col");
            columnEls.push(column);
            columnBoxes.push(col.nodes.map(function (n) { return n.id; }));
            column.setAttribute("aria-label", col.title);
            column.appendChild(el("h4", "ad-col-title", col.title));
            var lastGroup = null;
            col.nodes.forEach(function (n) {
                if (n.group && n.group !== lastGroup) {
                    column.appendChild(el("div", "ad-group", n.group));
                    lastGroup = n.group;
                }
                var box = el("button", "ad-node type-" + n.type + (n.muted ? " muted" : ""));
                box.type = "button";
                box.dataset.id = n.id;
                box.setAttribute("aria-pressed", "false");
                var count = linksOf[n.id].length;
                box.setAttribute("aria-label", n.label + ", " + (n.type_label || n.type) + ", " + count + " connection" + (count === 1 ? "" : "s"));

                var head = el("span", "ad-node-head");
                head.appendChild(el("span", "ad-dot"));
                head.appendChild(nameNode(n.label));
                box.appendChild(head);
                var sub = [n.type_label].concat(n.meta || []).filter(Boolean).join(" · ");
                if (sub) box.appendChild(el("span", "ad-sub", sub));
                if ((n.badges || []).length) {
                    var badges = el("span", "ad-badges");
                    n.badges.forEach(function (b) { badges.appendChild(el("span", "ad-badge", b)); });
                    box.appendChild(badges);
                }
                box.appendChild(el("span", "ad-count", count ? count + " connection" + (count === 1 ? "" : "s") : "no connections"));
                boxes[n.id] = box;
                column.appendChild(box);
            });
            grid.appendChild(column);
        });
        canvas.appendChild(grid);
        root.appendChild(canvas);

        var details = el("div", "ad-details");
        details.setAttribute("aria-live", "polite");
        root.appendChild(details);
        container.appendChild(root);

        // ---------- lines ----------
        var paths = [];

        function anchors(box, items, side, base) {
            // spread a box's lines over the middle of its edge, ordered by the other end's height
            var r = box.getBoundingClientRect();
            var out = {};
            items.sort(function (a, b) { return a.y - b.y; });
            items.forEach(function (item, k) {
                var t = items.length === 1 ? 0.5 : 0.25 + 0.5 * k / (items.length - 1);
                out[item.link._i] = {x: (side === "right" ? r.right : r.left) - base.left, y: r.top + r.height * t - base.top};
            });
            return out;
        }

        function draw() {
            paths.forEach(function (p) { p.remove(); });
            paths = [];
            if (getComputedStyle(svg).display === "none") return;

            var base = canvas.getBoundingClientRect();
            svg.setAttribute("width", canvas.scrollWidth);
            svg.setAttribute("height", canvas.scrollHeight);

            var mid = {};
            Object.keys(boxes).forEach(function (id) {
                var r = boxes[id].getBoundingClientRect();
                mid[id] = r.top + r.height / 2;
            });

            // which side each end of a link leaves from
            var ends = {};   // id -> {left: [], right: []}
            Object.keys(boxes).forEach(function (id) { ends[id] = {left: [], right: []}; });
            links.forEach(function (l) {
                var cs = columnOf[l.source], ct = columnOf[l.target];
                // same column: both ends on the left edge, the arc bulges into the gutter
                var sSide = cs < ct ? "right" : "left";
                var tSide = cs < ct ? "left" : (cs === ct ? "left" : "right");
                l._sides = [sSide, tSide];
                ends[l.source][sSide].push({link: l, y: mid[l.target]});
                ends[l.target][tSide].push({link: l, y: mid[l.source]});
            });
            var points = {};
            Object.keys(ends).forEach(function (id) {
                ["left", "right"].forEach(function (side) {
                    var a = anchors(boxes[id], ends[id][side], side, base);
                    Object.keys(a).forEach(function (i) {
                        points[i] = points[i] || {};
                        points[i][id === links[i].source ? "s" : "t"] = a[i];
                    });
                });
            });

            // free horizontal lanes (gaps between boxes) of every column, for links that skip columns
            var lanes = columnEls.map(function (colEl, i) {
                var rects = columnBoxes[i].map(function (id) { return boxes[id].getBoundingClientRect(); });
                var free = [], top = -Infinity;
                rects.forEach(function (r) {
                    free.push([top, r.top - base.top - 3]);
                    top = r.bottom - base.top + 3;
                });
                free.push([top, Infinity]);
                var c = colEl.getBoundingClientRect();
                return {left: c.left - base.left, right: c.right - base.left, free: free};
            });

            function laneY(from, to, want) {
                // intersect the free intervals of the skipped columns and take the one closest to `want`
                var free = [[-Infinity, Infinity]];
                for (var i = from; i <= to; i++) {
                    var next = [];
                    free.forEach(function (a) {
                        lanes[i].free.forEach(function (b) {
                            var lo = Math.max(a[0], b[0]), hi = Math.min(a[1], b[1]);
                            if (hi - lo >= 0) next.push([lo, hi]);
                        });
                    });
                    free = next;
                }
                var best = null, bestCost = Infinity;
                free.forEach(function (f) {
                    var lo = isFinite(f[0]) ? f[0] : f[1] - 14, hi = isFinite(f[1]) ? f[1] : f[0] + 14;
                    var y = Math.max(lo, Math.min(hi, want));
                    if (isFinite(f[0]) && isFinite(f[1])) y = (Math.abs(y - want) < 1 ? y : (lo + hi) / 2);
                    var cost = Math.abs(y - want);
                    if (cost < bestCost) { bestCost = cost; best = y; }
                });
                return best == null ? want : best;
            }

            function curve(a, b) {
                var dx = (b.x - a.x) / 2;
                return " C" + (a.x + dx) + "," + a.y + " " + (b.x - dx) + "," + b.y + " " + b.x + "," + b.y;
            }

            links.forEach(function (l) {
                var p = points[l._i], s = p.s, t = p.t;
                var cs = columnOf[l.source], ct = columnOf[l.target];
                var d;
                if (cs === ct) {
                    var bulge = 22 + Math.min(30, Math.abs(t.y - s.y) / 8);
                    d = "M" + s.x + "," + s.y + " C" + (s.x - bulge) + "," + s.y + " " + (t.x - bulge) + "," + t.y + " " + (t.x - 2) + "," + t.y;
                } else if (Math.abs(ct - cs) > 1) {
                    var lo = Math.min(cs, ct) + 1, hi = Math.max(cs, ct) - 1;
                    var y = laneY(lo, hi, (s.y + t.y) / 2);
                    var forward = cs < ct;
                    var enter = {x: forward ? lanes[lo].left - 6 : lanes[hi].right + 6, y: y};
                    var leave = {x: forward ? lanes[hi].right + 6 : lanes[lo].left - 6, y: y};
                    d = "M" + s.x + "," + s.y + curve(s, enter) + " L" + leave.x + "," + leave.y + curve(leave, t);
                } else {
                    d = "M" + s.x + "," + s.y + curve(s, t);
                }
                var path = document.createElementNS(SVG, "path");
                path.setAttribute("d", d);
                path.setAttribute("class", "ad-link cat-" + l.category);
                path.setAttribute("marker-end", "url(#ad-arrow-" + l.category + "-" + uid + ")");
                path.dataset.i = l._i;
                svg.appendChild(path);
                paths.push(path);
            });
            applyHighlight();
        }

        // ---------- highlight & details ----------
        var hovered = null, selected = null;

        function applyHighlight() {
            var id = hovered || selected;
            canvas.classList.toggle("ad-focus", !!id);
            var related = {};
            if (id) {
                related[id] = true;
                linksOf[id].forEach(function (l) { related[l.source] = related[l.target] = true; });
            }
            Object.keys(boxes).forEach(function (n) {
                boxes[n].classList.toggle("on", !!related[n]);
                boxes[n].setAttribute("aria-pressed", String(n === selected));
            });
            paths.forEach(function (p) {
                var l = links[p.dataset.i];
                p.classList.toggle("on", !!id && (l.source === id || l.target === id));
            });
        }

        function item(list, other, l) {
            var li = el("li");
            var name = el("button", "ad-link-btn", nodesById[other].label);
            name.type = "button";
            name.addEventListener("click", function () { select(other, true); });
            li.appendChild(name);
            li.appendChild(el("span", "ad-kinds", l.kinds.join(", ")));
            (l.details || []).slice(0, 3).forEach(function (d) { li.appendChild(el("div", "ad-detail", d)); });
            if ((l.details || []).length > 3) li.appendChild(el("div", "ad-detail", "+ " + (l.details.length - 3) + " more"));
            list.appendChild(li);
        }

        function showDetails(id) {
            details.innerHTML = "";
            if (!id) {
                details.appendChild(el("p", "small muted", "Select a box to list everything it is connected to."));
                return;
            }
            var n = nodesById[id];
            var head = el("div", "ad-details-head");
            var title = el("div");
            title.appendChild(el("h4", null, n.label));
            title.appendChild(el("div", "small muted", [n.type_label].concat(n.meta || []).filter(Boolean).join(" · ")));
            head.appendChild(title);
            var actions = el("div", "row");
            if (n.type === "module" && options.onOpenModule && n.path != null) {
                var open = el("button", "btn btn-sm", "View components");
                open.type = "button";
                open.addEventListener("click", function () { options.onOpenModule(n.path); });
                actions.appendChild(open);
            }
            var close = el("button", "btn btn-sm btn-ghost", "Clear");
            close.type = "button";
            close.addEventListener("click", function () { select(null); boxes[id].focus(); });
            actions.appendChild(close);
            head.appendChild(actions);
            details.appendChild(head);

            var sections = [
                ["Receives calls from", function (l) { return l.target === id && (l.category === "call" || l.category === "library"); }, "source"],
                ["Calls", function (l) { return l.source === id && l.category === "call"; }, "target"],
                ["Uses libraries", function (l) { return l.source === id && l.category === "library"; }, "target"],
                ["Data", function (l) { return l.source === id && l.category === "data"; }, "target"],
                ["Messaging", function (l) { return l.source === id && l.category === "messaging"; }, "target"],
                ["External calls", function (l) { return l.source === id && l.category === "external"; }, "target"],
                ["Used by", function (l) { return l.target === id && l.category !== "call" && l.category !== "library"; }, "source"],
            ];
            var grid = el("div", "ad-details-grid");
            var any = false;
            sections.forEach(function (s) {
                var matching = linksOf[id].filter(s[1]);
                if (!matching.length) return;
                any = true;
                var block = el("div");
                block.appendChild(el("h5", null, s[0] + " (" + matching.length + ")"));
                var list = el("ul");
                matching.forEach(function (l) { item(list, l[s[2]], l); });
                block.appendChild(list);
                grid.appendChild(block);
            });
            if (!any) grid.appendChild(el("p", "small muted", "No connections found in the scanned code."));
            details.appendChild(grid);
        }

        function select(id, focus) {
            selected = id === selected ? null : id;
            applyHighlight();
            showDetails(selected);
            if (focus && selected) boxes[selected].focus();
            // stacked (phone) layout: the details are far below the tapped box
            if (selected && getComputedStyle(svg).display === "none") details.scrollIntoView({block: "nearest", behavior: "smooth"});
        }

        Object.keys(boxes).forEach(function (id) {
            var box = boxes[id];
            box.addEventListener("mouseenter", function () { hovered = id; applyHighlight(); });
            box.addEventListener("mouseleave", function () { hovered = null; applyHighlight(); });
            box.addEventListener("focus", function () { hovered = id; applyHighlight(); });
            box.addEventListener("blur", function () { hovered = null; applyHighlight(); });
            box.addEventListener("click", function () { select(id); });
        });
        root.addEventListener("keydown", function (e) {
            if (e.key === "Escape" && selected) { var id = selected; select(null); boxes[id].focus(); }
        });

        showDetails(null);
        draw();
        if (window.ResizeObserver) new ResizeObserver(function () { draw(); }).observe(canvas);
        else window.addEventListener("resize", draw);
        return {redraw: draw, select: select};
    }

    window.ArchDiagram = {render: render};
})();
