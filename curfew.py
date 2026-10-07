"""COUVRE-FEU — a top-down stealth game set in Ouagadougou after curfew.

Move: WASD/arrows   Sneak: Ctrl   Sprint: Shift   Retry: R   Quit: Esc
"""
import math
import random
from collections import deque

import pygame

T = 32                      # tile size (px)
MW, MH = 90, 70             # map size (tiles)
SW, SH = 1024, 768          # screen size
GROUND, BUILDING, WATER = 0, 1, 2
LAMP_R = 120
DARKNESS = 242              # alpha of the night overlay

# name: detection radius (px), speed (px/s), uniform colour
POLICE_TYPES = {
    "Agent":          (110, 95,  (40, 70, 160)),
    "Gendarme":       (170, 70,  (30, 100, 60)),   # big torch, slow
    "Motard":         (85,  175, (210, 210, 210)), # fast, short sight
    "Gros Chef":      (60,  45,  (120, 70, 30)),   # barely sees, barely moves
}

DEATH_CAPTIONS = [
    "Official report: the suspect slipped on a banana peel. 14 times.",
    "Cause of death: resisted arrest by breathing.",
    "The baton has been nominated for a medal of honour.",
    "He was only buying bread. Bread is illegal after 21h.",
    "Autopsy: natural causes. Police are natural in Ouaga.",
    "Family will receive the bill for the broken baton.",
]


# ---------------------------------------------------------------- map
def build_map(seed=226):
    """Rough Ouagadougou: barrages up north, grid sectors, lit boulevards,
    Place des Nations Unies roundabout in the middle, a diagonal avenue."""
    rnd = random.Random(seed)
    g = [[BUILDING] * MW for _ in range(MH)]

    def dig(x, y, v=GROUND):
        if 0 <= x < MW and 0 <= y < MH:
            g[y][x] = v

    # sector street grid (2-wide streets every 7 tiles)
    for y in range(MH):
        for x in range(MW):
            if x % 7 < 2 or y % 7 < 2:
                g[y][x] = GROUND
    # courtyards / alleys / vacant lots
    for _ in range(260):
        bx, by = rnd.randrange(MW), rnd.randrange(MH)
        for dx in range(rnd.randint(1, 3)):
            dig(bx + dx, by)
    # barrages (water) along the north
    for x in range(MW):
        top = 2 + int(2 * math.sin(x / 6))
        bot = 7 + int(2 * math.sin(x / 4 + 1))
        if 12 < x < 78:
            for y in range(top, bot):
                dig(x, y, WATER)
    lamps = []
    # boulevards: horizontal (Kwame Nkrumah-ish) + vertical (Charles de Gaulle-ish)
    for x in range(MW):
        for y in (34, 35, 36):
            dig(x, y)
        if x % 5 == 0:
            lamps.append((x * T + T // 2, 34 * T))
    for y in range(8, MH):
        for x in (44, 45, 46):
            dig(x, y)
        if y % 5 == 0:
            lamps.append((44 * T, y * T + T // 2))
    # diagonal avenue to the south-east
    for i in range(40):
        for w in range(3):
            dig(45 + i + w, 35 + i)
        if i % 6 == 0:
            lamps.append(((45 + i) * T, (35 + i) * T))
    # roundabout: Place des Nations Unies
    cx, cy = 45, 35
    for y in range(cy - 5, cy + 6):
        for x in range(cx - 5, cx + 6):
            d = math.hypot(x - cx, y - cy)
            if d <= 5:
                dig(x, y, BUILDING if d <= 1.5 else GROUND)
    for a in range(0, 360, 45):
        lamps.append((cx * T + math.cos(math.radians(a)) * 4 * T,
                      cy * T + math.sin(math.radians(a)) * 4 * T))
    # maquis lights scattered at random intersections
    for _ in range(18):
        lamps.append((rnd.randrange(0, MW, 7) * T + T, rnd.randrange(0, MH, 7) * T + T))
    # make sure start and home are walkable
    for x, y in ((3, 66), (85, 12)):
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                dig(x + dx, y + dy)
    return g, lamps


LABELS = [
    ("Barrage n°3", 20, 4), ("Barrage n°2", 40, 4), ("Barrage n°1", 62, 4),
    ("Place des Nations Unies", 40, 41), ("Av. Kwame Nkrumah", 12, 33),
    ("Bd Charles de Gaulle", 47, 55), ("Gounghin", 5, 60), ("Marché Rood Woko", 30, 28),
    ("Ouaga 2000", 70, 64), ("Patte d'Oie", 15, 46), ("MAISON", 83, 10),
]


def solid(g, px, py):
    tx, ty = int(px // T), int(py // T)
    return not (0 <= tx < MW and 0 <= ty < MH) or g[ty][tx] == BUILDING


def line_of_sight(g, a, b):
    d = math.dist(a, b)
    steps = max(1, int(d // 8))
    for i in range(1, steps):
        t = i / steps
        if solid(g, a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t):
            return False
    return True


def bfs(g, start, goal, limit=4000):
    """Tile path from start to goal (both tile coords), or [] if none."""
    prev = {start: None}
    q = deque([start])
    while q and len(prev) < limit:
        cur = q.popleft()
        if cur == goal:
            path = []
            while cur != start:
                path.append(cur)
                cur = prev[cur]
            return path[::-1]
        x, y = cur
        for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if n not in prev and 0 <= n[0] < MW and 0 <= n[1] < MH and g[n[1]][n[0]] != BUILDING:
                prev[n] = cur
                q.append(n)
    return []


def move_with_collision(g, pos, vel, dt, rad):
    """Axis-separated movement; water is walkable but slow (wading)."""
    for axis in (0, 1):
        nxt = list(pos)
        nxt[axis] += vel[axis] * dt
        if not any(solid(g, nxt[0] + ox, nxt[1] + oy) for ox in (-rad, rad) for oy in (-rad, rad)):
            pos[axis] = nxt[axis]


# ---------------------------------------------------------------- actors
class Cop:
    def __init__(self, kind, pos):
        self.kind = kind
        self.radius, self.speed, self.color = POLICE_TYPES[kind]
        self.pos = list(pos)
        self.path = []
        self.alert = 0.0          # 0..1, chase at 1
        self.chasing = False
        self.lost_for = 0.0
        self.repath = 0.0

    def tile(self):
        return int(self.pos[0] // T), int(self.pos[1] // T)

    def update(self, dt, g, player_pos, seen):
        if seen:
            self.alert = min(1.0, self.alert + dt * 2.5)
        else:
            self.alert = max(0.0, self.alert - dt * 0.4)
        if self.alert >= 1:
            self.chasing, self.lost_for = True, 0.0
        elif self.chasing:
            self.lost_for += dt
            if self.lost_for > 4:
                self.chasing, self.path = False, []

        self.repath -= dt
        if self.chasing and self.repath <= 0:
            self.repath = 0.4
            self.path = bfs(g, self.tile(), (int(player_pos[0] // T), int(player_pos[1] // T)))
        elif not self.path:
            tx, ty = self.tile()
            for _ in range(10):  # wander to a nearby random walkable tile
                goal = (tx + random.randint(-8, 8), ty + random.randint(-8, 8))
                if 0 <= goal[0] < MW and 0 <= goal[1] < MH and g[goal[1]][goal[0]] == GROUND:
                    self.path = bfs(g, (tx, ty), goal, 600)
                    break

        if self.path:
            tx, ty = self.path[0]
            target = (tx * T + T / 2, ty * T + T / 2)
            dx, dy = target[0] - self.pos[0], target[1] - self.pos[1]
            d = math.hypot(dx, dy)
            sp = self.speed * (1.25 if self.chasing else 0.6)
            if d < 4:
                self.path.pop(0)
            else:
                self.pos[0] += dx / d * sp * dt
                self.pos[1] += dy / d * sp * dt


# ---------------------------------------------------------------- rendering helpers
_light_cache = {}


def light_tex(r, strength=DARKNESS):
    key = (r, strength)
    if key not in _light_cache:
        s = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
        for i in range(r, 0, -2):
            a = int(strength * (1 - (i / r) ** 2))
            pygame.draw.circle(s, (0, 0, 0, a), (r, r), i)
        _light_cache[key] = s
    return _light_cache[key]


def draw_person(surf, pos, color, skin=(90, 55, 30), squash=1.0):
    x, y = int(pos[0]), int(pos[1])
    pygame.draw.ellipse(surf, color, (x - 9, y - 9 * squash, 18, 18 * squash))
    pygame.draw.circle(surf, skin, (x, y - int(4 * squash)), 5 if squash > .4 else 3)


def draw_ghost(surf, pos, t, font):
    x, y = pos
    ghost = pygame.Surface((40, 50), pygame.SRCALPHA)
    pygame.draw.circle(ghost, (240, 240, 255, 180), (20, 18), 16)
    pygame.draw.rect(ghost, (240, 240, 255, 180), (4, 18, 32, 20))
    for i in range(4):
        pygame.draw.circle(ghost, (240, 240, 255, 180), (8 + i * 8, 38 + int(3 * math.sin(t * 8 + i))), 4)
    pygame.draw.circle(ghost, (0, 0, 0), (14, 16), 3)
    pygame.draw.circle(ghost, (0, 0, 0), (26, 16), 3)
    pygame.draw.ellipse(ghost, (255, 220, 80), (8, 0, 24, 6), 2)  # halo
    surf.blit(ghost, (x - 20, y - 25))


# ---------------------------------------------------------------- game
class Game:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption("Couvre-feu — Ouagadougou")
        self.screen = pygame.display.set_mode((SW, SH))
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("dejavusans", 16)
        self.big = pygame.font.SysFont("dejavusans", 42, bold=True)
        self.grid, self.lamps = build_map()
        self.world = self.render_world()
        self.reset()

    def render_world(self):
        w = pygame.Surface((MW * T, MH * T))
        rnd = random.Random(1)
        colors = {GROUND: (150, 95, 60), WATER: (30, 60, 90)}   # laterite red dirt
        for y in range(MH):
            for x in range(MW):
                v = self.grid[y][x]
                r = pygame.Rect(x * T, y * T, T, T)
                if v == BUILDING:
                    c = rnd.choice([(170, 150, 120), (190, 170, 140), (120, 110, 100), (160, 90, 70)])
                    pygame.draw.rect(w, c, r)
                    pygame.draw.rect(w, (80, 70, 60), r, 1)
                else:
                    c = colors[v]
                    pygame.draw.rect(w, [max(0, min(255, k + rnd.randint(-8, 8))) for k in c], r)
        for lx, ly in self.lamps:
            pygame.draw.circle(w, (60, 60, 60), (int(lx), int(ly)), 4)
        for text, x, y in LABELS:
            col = (255, 230, 120) if text == "MAISON" else (240, 240, 220)
            w.blit(self.font.render(text, True, col), (x * T, y * T))
        return w

    def reset(self):
        self.player = [3 * T + T / 2, 66 * T + T / 2]
        self.home = pygame.Rect(84 * T, 11 * T, 3 * T, 3 * T)
        rnd = random.Random()
        self.cops = []
        kinds = list(POLICE_TYPES)
        while len(self.cops) < 28:
            x, y = rnd.randrange(MW), rnd.randrange(MH)
            if self.grid[y][x] == GROUND and math.hypot(x - 3, y - 66) > 15:
                self.cops.append(Cop(rnd.choice(kinds), (x * T + T / 2, y * T + T / 2)))
        self.state = "play"
        self.t = 0.0                 # seconds since curfew (21:00)
        self.death_t = 0.0
        self.caption = ""

    # ---- update
    def player_lit(self):
        return any(math.dist(self.player, l) < LAMP_R * 0.8 for l in self.lamps)

    def update(self, dt):
        if self.state != "play":
            self.death_t += dt
            return
        self.t += dt
        k = pygame.key.get_pressed()
        mx = (k[pygame.K_d] or k[pygame.K_RIGHT]) - (k[pygame.K_a] or k[pygame.K_LEFT])
        my = (k[pygame.K_s] or k[pygame.K_DOWN]) - (k[pygame.K_w] or k[pygame.K_UP])
        mods = pygame.key.get_mods()
        speed, vis = 120, 1.0
        if mods & pygame.KMOD_CTRL:
            speed, vis = 60, 0.6
        elif mods & pygame.KMOD_SHIFT:
            speed, vis = 200, 1.4
        if self.grid[int(self.player[1] // T)][int(self.player[0] // T)] == WATER:
            speed *= 0.5  # wading through the barrage
        n = math.hypot(mx, my) or 1
        move_with_collision(self.grid, self.player, (mx / n * speed, my / n * speed), dt, 7)
        if self.player_lit():
            vis *= 1.8
        self.moving = bool(mx or my)

        for c in self.cops:
            d = math.dist(c.pos, self.player)
            seen = d < c.radius * vis and line_of_sight(self.grid, c.pos, self.player)
            c.update(dt, self.grid, self.player, seen)
            if d < 16:
                self.state, self.death_t = "dead", 0.0
                self.caption = random.choice(DEATH_CAPTIONS)
        if self.home.collidepoint(self.player):
            self.state = "home"

    # ---- draw
    def draw(self):
        cam = (int(self.player[0] - SW / 2), int(self.player[1] - SH / 2))
        if self.state == "dead" and self.death_t < 4:  # screen shake while beating
            cam = (cam[0] + random.randint(-4, 4), cam[1] + random.randint(-4, 4))
        s = self.screen
        s.fill((0, 0, 0))
        s.blit(self.world, (-cam[0], -cam[1]))

        def sp(p):
            return p[0] - cam[0], p[1] - cam[1]

        pygame.draw.rect(s, (255, 220, 80), self.home.move(-cam[0], -cam[1]), 2)
        for c in self.cops:
            draw_person(s, sp(c.pos), c.color)
        if self.state == "play" or self.state == "home":
            draw_person(s, sp(self.player), (200, 40, 40))

        # night overlay with light holes
        dark = pygame.Surface((SW, SH), pygame.SRCALPHA)
        dark.fill((5, 5, 25, DARKNESS))
        for l in self.lamps:
            x, y = sp(l)
            if -LAMP_R < x < SW + LAMP_R and -LAMP_R < y < SH + LAMP_R:
                dark.blit(light_tex(LAMP_R), (x - LAMP_R, y - LAMP_R), special_flags=pygame.BLEND_RGBA_SUB)
        for c in self.cops:
            x, y = sp(c.pos)
            r = c.radius
            if -r < x < SW + r and -r < y < SH + r:
                dark.blit(light_tex(r), (x - r, y - r), special_flags=pygame.BLEND_RGBA_SUB)
        px, py = sp(self.player)
        dark.blit(light_tex(70, 120), (px - 70, py - 70), special_flags=pygame.BLEND_RGBA_SUB)
        s.blit(dark, (0, 0))

        # cop alert markers + siren flash when chasing
        for c in self.cops:
            x, y = sp(c.pos)
            if c.chasing:
                col = (255, 40, 40) if int(self.t * 6) % 2 else (40, 80, 255)
                pygame.draw.circle(s, col, (int(x), int(y) - 16), 4)
                s.blit(self.font.render("!", True, (255, 60, 60)), (x + 6, y - 30))
            elif c.alert > 0.05:
                s.blit(self.font.render("?", True, (255, 220, 80)), (x + 6, y - 30))

        if self.state == "dead":
            self.draw_death(sp(self.player))
        elif self.state == "home":
            self.banner("Tu es rentré. Maman est quand même fâchée.", "R to play again")
        self.hud()
        pygame.display.flip()

    def draw_death(self, p):
        t, s = self.death_t, self.screen
        x, y = p
        squash = max(0.15, 1 - max(0, t - 1) / 3)
        draw_person(s, p, (200, 40, 40), squash=squash)
        # three cops close in, then swing batons
        for i in range(3):
            a = i * 2.1
            dist = 60 - min(1, t) * 35
            cx, cy = x + math.cos(a) * dist, y + math.sin(a) * dist
            draw_person(s, (cx, cy), (40, 70, 160))
            if 1 < t < 4:
                swing = a + math.pi + math.sin(t * 18 + i) * 0.9
                pygame.draw.line(s, (20, 20, 20), (cx, cy),
                                 (cx + math.cos(swing) * 26, cy + math.sin(swing) * 26), 4)
        if 1 < t < 4 and int(t * 5) % 2:
            word = ["PAF!", "BAM!", "KPA!", "POW!"][int(t * 5) % 4]
            s.blit(self.big.render(word, True, (255, 230, 0)), (x + 25, y - 70))
        if t > 4:
            draw_ghost(s, (x, y - (t - 4) * 30), t, self.font)
        if t > 4.5:
            self.banner("ARRÊTÉ.", self.caption + "   [R] retry")

    def banner(self, title, sub):
        box = pygame.Surface((SW, 120), pygame.SRCALPHA)
        box.fill((0, 0, 0, 200))
        self.screen.blit(box, (0, SH - 160))
        tt = self.big.render(title, True, (255, 255, 255))
        st = self.font.render(sub, True, (230, 230, 230))
        self.screen.blit(tt, (SW / 2 - tt.get_width() / 2, SH - 150))
        self.screen.blit(st, (SW / 2 - st.get_width() / 2, SH - 90))

    def hud(self):
        mins = 21 * 60 + int(self.t * 2)          # 1 real second = 2 game minutes
        clock = f"{(mins // 60) % 24:02d}:{mins % 60:02d}"
        lit = "  ÉCLAIRÉ!" if self.state == "play" and self.player_lit() else ""
        txt = f"COUVRE-FEU {clock}   Rentre à la MAISON (nord-est){lit}"
        self.screen.blit(self.font.render(txt, True, (255, 255, 255)), (12, 10))
        self.screen.blit(self.font.render("WASD move · Ctrl sneak · Shift sprint", True, (160, 160, 160)), (12, 30))

    def run(self):
        while True:
            dt = min(self.clock.tick(60) / 1000, 0.05)
            for e in pygame.event.get():
                if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
                    return
                if e.type == pygame.KEYDOWN and e.key == pygame.K_r and self.state != "play":
                    self.reset()
            self.update(dt)
            self.draw()


def selfcheck():
    g, _ = build_map()
    assert g[66][3] == GROUND and g[12][85] == GROUND
    assert bfs(g, (3, 66), (85, 12), limit=MW * MH), "home must be reachable from start"


if __name__ == "__main__":
    selfcheck()
    Game().run()
