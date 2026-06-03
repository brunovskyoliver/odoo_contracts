/** @odoo-module **/

import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("contract_timer_systray_tour", {
    test: true,
    url: "/odoo",
    steps: () => [
        {
            content: "Ikona časovača v hornej lište je viditeľná",
            trigger: ".contract_timer_systray .fa-hourglass-half",
        },
        {
            content: "Spustiť časovač",
            trigger: ".contract_timer_systray .contract_timer_button:not(.is-running):not(.is-paused)",
            run: "click",
        },
        {
            content: "Spustený časovač je viditeľný",
            trigger: ".contract_timer_systray .contract_timer_button.is-running .contract_timer_elapsed",
        },
        {
            content: "Otvoriť dialóg spusteného časovača",
            trigger: ".contract_timer_systray .contract_timer_button.is-running",
            run: "click",
        },
        {
            content: "Akcia pozastavenia je dostupná",
            trigger: ".modal .contract_timer_pause",
        },
        {
            content: "Akcia vymazania je dostupná bez uloženia času",
            trigger: ".modal .contract_timer_clear",
        },
        {
            content: "Pozastaviť časovač",
            trigger: ".modal .contract_timer_pause",
            run: "click",
        },
        {
            content: "Pozastavený časovač je viditeľný",
            trigger: ".contract_timer_systray .contract_timer_button.is-paused",
        },
        {
            content: "Otvoriť dialóg pozastaveného časovača",
            trigger: ".contract_timer_systray .contract_timer_button.is-paused",
            run: "click",
        },
        {
            content: "Akcia pokračovania je dostupná",
            trigger: ".modal .contract_timer_resume",
        },
        {
            content: "Vymazať pozastavený časovač",
            trigger: ".modal .contract_timer_clear",
            run: "click",
        },
        {
            content: "Časovač sa vráti do nečinného stavu",
            trigger: ".contract_timer_systray .contract_timer_button:not(.is-running):not(.is-paused)",
        },
    ],
});
