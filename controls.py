"""Input for Couvre-feu: the keyboard, plus an on-screen joystick and buttons for touch screens
(the mouse drives them too, so they can be tried on PC).

The game never reads keys or touches itself. Each frame it asks for an Intent (what the player is
doing right now) and drains the commands (one-off presses: act, use an item, tap, restart, quit).
"""
import math
from collections import defaultdict
from dataclasses import dataclass

import pygame


@dataclass
class Intent:
    move: tuple = (0.0, 0.0)   # direction, length 0..1
    sneak: bool = False
    sprint: bool = False
    act: bool = False          # held: knock, search, talk


class Controls:
    def __init__(self, size, cfg, font):
        self.w, self.h = size
        self.cfg = cfg
        self.font = font
        self.shown = cfg["touch_ui"] == "always"   # "auto" shows them on the first touch or click
        self.pointers = {}       # pointer id -> {"role": "stick" | button name, "base", "pos"}
        self.commands = []
        r = cfg["button_radius"]
        # name -> (centre, radius, label); kept clear of the dialogue box along the bottom
        self.buttons = {"act": ((self.w - r - 30, self.h - r - 175), r, "Agir"),
                        "sprint": ((self.w - 2 * r - int(r * 0.8) - 55, self.h - int(r * 0.8) - 165), int(r * 0.8), "Sprint")}
        self.idle_stick = (cfg["stick_radius"] + 40, self.h - cfg["stick_radius"] - 175)

    # ---- events
    def handle(self, e):
        if e.type == pygame.QUIT:
            self.commands.append(("quit",))
        elif e.type == pygame.KEYDOWN:
            if e.key == pygame.K_ESCAPE:
                self.commands.append(("quit",))
            elif e.key == pygame.K_r:
                self.commands.append(("restart",))
            elif e.key == pygame.K_e:
                self.commands.append(("act",))
            elif pygame.K_1 <= e.key <= pygame.K_9:
                self.commands.append(("use", e.key - pygame.K_1))
        elif e.type == pygame.FINGERDOWN:
            self.press(("finger", e.finger_id), (e.x * self.w, e.y * self.h))
        elif e.type == pygame.FINGERMOTION:
            self.move(("finger", e.finger_id), (e.x * self.w, e.y * self.h))
        elif e.type == pygame.FINGERUP:
            self.pointers.pop(("finger", e.finger_id), None)
        elif getattr(e, "touch", False):
            pass                 # a mouse event the OS made up from a touch: already handled as a finger
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            self.press("mouse", e.pos)
        elif e.type == pygame.MOUSEMOTION:
            self.move("mouse", e.pos)
        elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
            self.pointers.pop("mouse", None)

    def press(self, pid, pos):
        if self.cfg["touch_ui"] == "never":
            self.commands.append(("tap", pos))
            return
        self.shown = True
        for name, (centre, r, _) in self.buttons.items():
            if math.dist(pos, centre) <= r * 1.2:          # a little slack for fat fingers
                self.pointers[pid] = {"role": name}
                if name == "act":
                    self.commands.append(("act",))
                return
        in_zone = pos[0] < self.w / 2 and pos[1] > self.h * 0.35   # lower left, clear of the HUD
        if in_zone and not any(p["role"] == "stick" for p in self.pointers.values()):
            self.pointers[pid] = {"role": "stick", "base": pos, "pos": pos}   # the stick appears under your thumb
        else:
            self.commands.append(("tap", pos))

    def move(self, pid, pos):
        if pid in self.pointers:
            self.pointers[pid]["pos"] = pos

    def take(self):
        cmds, self.commands = self.commands, []
        return cmds

    # ---- state
    def stick(self):
        """Joystick push as (x, y), length 0..1, or None when no thumb is on it."""
        for p in self.pointers.values():
            if p["role"] == "stick":
                dx, dy = p["pos"][0] - p["base"][0], p["pos"][1] - p["base"][1]
                d = math.hypot(dx, dy)
                push = min(1.0, d / self.cfg["stick_radius"])
                return (0.0, 0.0) if push < self.cfg["deadzone"] else (dx / d * push, dy / d * push)
        return None

    def held(self, role):
        return any(p["role"] == role for p in self.pointers.values())

    def intent(self, keys=None, mods=None):
        keys = pygame.key.get_pressed() if keys is None else keys
        mods = pygame.key.get_mods() if mods is None else mods
        kx = (keys[pygame.K_d] or keys[pygame.K_RIGHT]) - (keys[pygame.K_a] or keys[pygame.K_LEFT])
        ky = (keys[pygame.K_s] or keys[pygame.K_DOWN]) - (keys[pygame.K_w] or keys[pygame.K_UP])
        stick = self.stick()
        move, push = (kx, ky), None
        if not (kx or ky) and stick:
            move = stick
            push = math.hypot(*stick)
        n = math.hypot(*move)
        if n > 1:
            move = (move[0] / n, move[1] / n)
        return Intent(move=move,
                      sneak=bool(mods & pygame.KMOD_CTRL) or (push is not None and 0 < push < self.cfg["sneak_below"]),
                      sprint=bool(mods & pygame.KMOD_SHIFT) or self.held("sprint"),
                      act=bool(keys[pygame.K_e]) or self.held("act"))

    # ---- drawing
    def draw(self, surf):
        if not self.shown:
            return
        R = self.cfg["stick_radius"]
        stick = next((p for p in self.pointers.values() if p["role"] == "stick"), None)
        base = stick["base"] if stick else self.idle_stick
        _circle(surf, base, R, (255, 255, 255, 40 if stick else 22))
        _circle(surf, base, R, (255, 255, 255, 110), 2)
        push = self.stick() or (0, 0)
        knob = (base[0] + push[0] * R, base[1] + push[1] * R)
        _circle(surf, knob, R // 2.4, (255, 255, 255, 150 if stick else 60))
        if stick and 0 < math.hypot(*push) < self.cfg["sneak_below"]:
            _circle(surf, base, R * self.cfg["sneak_below"], (150, 200, 240, 90), 1)   # the sneak ring
        for name, (centre, r, label) in self.buttons.items():
            on = self.held(name)
            _circle(surf, centre, r, (230, 200, 140, 120 if on else 45))
            _circle(surf, centre, r, (230, 200, 140, 170), 2)
            text = self.font.render(label, True, (255, 255, 255))
            surf.blit(text, text.get_rect(center=centre))


def _circle(surf, centre, r, rgba, width=0):
    r = int(r)
    s = pygame.Surface((2 * r + 2, 2 * r + 2), pygame.SRCALPHA)
    pygame.draw.circle(s, rgba, (r + 1, r + 1), r, width)
    surf.blit(s, (centre[0] - r - 1, centre[1] - r - 1))


def selfcheck(cfg):
    """Drive the controls with fake touches and keys; no window needed."""
    c = Controls((1024, 768), dict(cfg, touch_ui="auto"), font=None)
    no_keys, no_mods = defaultdict(bool), 0     # nothing pressed (key codes go up to ~2**30)

    def finger(kind, fid, x, y):
        c.handle(pygame.event.Event(kind, finger_id=fid, x=x / 1024, y=y / 768, dx=0, dy=0))

    finger(pygame.FINGERDOWN, 1, 200, 600)                       # thumb down, lower left: the stick
    finger(pygame.FINGERMOTION, 1, 200 + cfg["stick_radius"] * 3, 600)
    i = c.intent(no_keys, no_mods)
    assert abs(i.move[0] - 1) < 1e-9 and i.move[1] == 0 and not i.sneak, i   # pushed past the rim: full walk right
    finger(pygame.FINGERMOTION, 1, 200, 600 - cfg["stick_radius"] * 0.3)
    i = c.intent(no_keys, no_mods)
    assert i.move[1] < 0 and i.sneak, i                         # small push up: sneak
    finger(pygame.FINGERMOTION, 1, 202, 600)
    assert c.intent(no_keys, no_mods).move == (0.0, 0.0)        # inside the deadzone
    (ax, ay), _, _ = c.buttons["act"]
    finger(pygame.FINGERDOWN, 2, ax, ay)                         # second thumb on Agir
    assert c.intent(no_keys, no_mods).act and c.take() == [("act",)]
    finger(pygame.FINGERUP, 2, ax, ay)
    finger(pygame.FINGERUP, 1, 202, 600)
    assert c.intent(no_keys, no_mods) == Intent()
    finger(pygame.FINGERDOWN, 3, 900, 100)                       # upper right: a plain tap
    assert c.take() == [("tap", (900.0, 100.0))]
    c.handle(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(200, 600), touch=True))
    assert not c.pointers                                        # mouse echo of a touch: ignored
    c.handle(pygame.event.Event(pygame.MOUSEBUTTONDOWN, button=1, pos=(200, 600), touch=False))
    assert c.held("stick")                                       # a real mouse drives the stick on PC
