/** @odoo-module **/

import { Dialog } from "@web/core/dialog/dialog";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { sprintf } from "@web/core/utils/strings";
import {
    Component,
    onMounted,
    onWillDestroy,
    onWillStart,
    useState,
} from "@odoo/owl";

const TIMER_MODEL = "contract.timer.session";

export class TimerStopDialog extends Component {
    static template = "contract.TimerStopDialog";
    static components = { Dialog };
    static props = {
        close: { type: Function, optional: true },
        timerState: { type: String, optional: true },
        onChanged: { type: Function, optional: true },
        onStopped: { type: Function, optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.state = useState({
            loading: true,
            saving: false,
            tickets: [],
            ticketId: false,
            taskLoading: false,
            tasks: [],
            taskId: false,
            willCreateTask: false,
            requiresTask: false,
            description: "",
            doubleRate: false,
        });

        onWillStart(async () => {
            let tickets = [];
            try {
                tickets = await this.orm.call(TIMER_MODEL, "action_get_open_tickets", []);
            } catch {
                this.notification.add(_t("Časovač zatiaľ nie je dostupný. Aktualizujte modul."), {
                    type: "warning",
                });
                this.props.close();
                return;
            }
            this.state.tickets = tickets;
            this.state.ticketId = tickets.length ? tickets[0].id : false;
            this.state.loading = false;
            if (this.state.ticketId) {
                await this.loadTicketTasks();
            }
        });
    }

    async loadTicketTasks() {
        if (!this.state.ticketId) {
            this.state.tasks = [];
            this.state.taskId = false;
            this.state.willCreateTask = false;
            this.state.requiresTask = false;
            return;
        }
        this.state.taskLoading = true;
        let taskInfo;
        try {
            taskInfo = await this.orm.call(TIMER_MODEL, "action_get_ticket_timer_tasks", [
                this.state.ticketId,
            ]);
        } catch {
            this.state.taskLoading = false;
            this.state.tasks = [];
            this.state.taskId = false;
            this.state.willCreateTask = false;
            this.state.requiresTask = true;
            this.notification.add(_t("Nepodarilo sa načítať úlohy pre tento tiket."), {
                type: "danger",
            });
            return;
        }
        this.state.tasks = taskInfo.tasks || [];
        this.state.taskId = taskInfo.task_id || false;
        this.state.willCreateTask = Boolean(taskInfo.will_create_task);
        this.state.requiresTask = Boolean(taskInfo.requires_task);
        this.state.taskLoading = false;
    }

    async onTicketChange(ev) {
        this.state.ticketId = parseInt(ev.target.value, 10) || false;
        this.state.taskId = false;
        await this.loadTicketTasks();
    }

    onTaskChange(ev) {
        this.state.taskId = parseInt(ev.target.value, 10) || false;
    }

    onDescriptionInput(ev) {
        this.state.description = ev.target.value;
    }

    get canSave() {
        return Boolean(
            !this.state.loading
            && !this.state.saving
            && !this.state.taskLoading
            && this.state.ticketId
            && this.state.description.trim()
            && (!this.state.requiresTask || this.state.taskId)
        );
    }

    async onConfirm() {
        if (!this.canSave) {
            return;
        }
        this.state.saving = true;
        let result;
        try {
            result = await this.orm.call(TIMER_MODEL, "action_stop_timer", [
                this.state.ticketId,
                this.state.doubleRate,
                this.state.taskId,
                this.state.description,
            ]);
        } catch {
            this.state.saving = false;
            this.notification.add(_t("Čas sa nepodarilo uložiť. Skontrolujte prístup a vybraný tiket."), {
                type: "danger",
            });
            return;
        }
        this.notification.add(
            sprintf(_t("Čas uložený: %s fakturovateľných hodín."), result.billable_hours.toFixed(2)),
            { type: "success" }
        );
        if (this.props.onChanged) {
            await this.props.onChanged(result);
        } else if (this.props.onStopped) {
            await this.props.onStopped(result);
        }
        this.props.close();
    }

    async runTimerAction(method, successMessage) {
        if (this.state.saving) {
            return;
        }
        this.state.saving = true;
        try {
            await this.orm.call(TIMER_MODEL, method, []);
        } catch {
            this.state.saving = false;
            this.notification.add(_t("Časovač sa nepodarilo upraviť. Obnovte stránku a skúste to znova."), {
                type: "danger",
            });
            return;
        }
        this.notification.add(successMessage, { type: "success" });
        if (this.props.onChanged) {
            await this.props.onChanged();
        }
        this.props.close();
    }

    async onPause() {
        await this.runTimerAction("action_pause_timer", _t("Časovač pozastavený."));
    }

    async onResume() {
        await this.runTimerAction("action_resume_timer", _t("Časovač pokračuje."));
    }

    async onClear() {
        await this.runTimerAction("action_clear_timer", _t("Časovač vymazaný."));
    }
}

export class TimerSystray extends Component {
    static template = "contract.TimerSystray";

    setup() {
        this.orm = useService("orm");
        this.dialog = useService("dialog");
        this.notification = useService("notification");
        this.state = useState({
            loading: true,
            available: true,
            running: false,
            paused: false,
            timerState: "idle",
            sessionId: false,
            startDatetime: false,
            serverDatetime: false,
            baselineElapsedSeconds: 0,
            baselineClientTimestamp: Date.now(),
            displayTimestamp: Date.now(),
        });
        this.interval = null;
        this.onVisibilityChange = () => {
            if (!document.hidden) {
                this.state.displayTimestamp = Date.now();
            }
        };

        onWillStart(async () => {
            await this.refreshStatus();
        });
        onMounted(() => {
            this.interval = setInterval(() => {
                if (this.state.running) {
                    this.state.displayTimestamp = Date.now();
                }
            }, 1000);
            document.addEventListener("visibilitychange", this.onVisibilityChange);
        });
        onWillDestroy(() => {
            if (this.interval) {
                clearInterval(this.interval);
            }
            document.removeEventListener("visibilitychange", this.onVisibilityChange);
        });
    }

    applyStatus(status) {
        const now = Date.now();
        this.state.available = true;
        this.state.running = status.running;
        this.state.paused = status.paused;
        this.state.timerState = status.state || (status.running ? "running" : "idle");
        this.state.sessionId = status.session_id;
        this.state.startDatetime = status.start_datetime;
        this.state.serverDatetime = status.server_datetime;
        this.state.baselineElapsedSeconds = status.elapsed_seconds || 0;
        this.state.baselineClientTimestamp = now;
        this.state.displayTimestamp = now;
        this.state.loading = false;
    }

    async refreshStatus() {
        let status;
        try {
            status = await this.orm.call(TIMER_MODEL, "action_get_timer_status", []);
        } catch {
            this.state.available = false;
            this.state.loading = false;
            return;
        }
        this.applyStatus(status);
    }

    get displayElapsedSeconds() {
        const baselineElapsedSeconds = this.state.baselineElapsedSeconds || 0;
        if (!this.state.running) {
            return baselineElapsedSeconds;
        }
        const elapsedSinceBaseline = Math.floor(
            Math.max(
                (this.state.displayTimestamp || Date.now())
                - (this.state.baselineClientTimestamp || Date.now()),
                0
            ) / 1000
        );
        return baselineElapsedSeconds + elapsedSinceBaseline;
    }

    get elapsedLabel() {
        const totalSeconds = Math.max(this.displayElapsedSeconds, 0);
        const hours = Math.floor(totalSeconds / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        const seconds = totalSeconds % 60;
        if (hours) {
            return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
        }
        return `${minutes}:${String(seconds).padStart(2, "0")}`;
    }

    async onClick() {
        if (this.state.loading || !this.state.available) {
            return;
        }
        if (!this.state.running && !this.state.paused) {
            this.state.loading = true;
            let status;
            try {
                status = await this.orm.call(TIMER_MODEL, "action_start_timer", []);
            } catch {
                this.state.available = false;
                this.state.loading = false;
                return;
            }
            this.applyStatus(status);
            this.notification.add(_t("Časovač spustený."), { type: "success" });
            return;
        }
        this.dialog.add(TimerStopDialog, {
            timerState: this.state.timerState,
            onChanged: async () => {
                await this.refreshStatus();
            },
            onStopped: async () => {
                await this.refreshStatus();
            },
        });
    }
}

registry.category("systray").add(
    "contract.TimerSystray",
    { Component: TimerSystray },
    { sequence: 50 }
);
