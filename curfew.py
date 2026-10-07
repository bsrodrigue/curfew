"""COUVRE-FEU — a top-down stealth game set in Ouagadougou after curfew.

Move: WASD/arrows   Sneak: Ctrl   Sprint: Shift   Knock: hold E at a door   Retry: R   Quit: Esc
"""
import math
import random
import tomllib
from collections import deque
from pathlib import Path

import pygame

CFG = tomllib.loads(Path(__file__).with_name("config.toml").read_text(encoding="utf-8"))

T = 32                      # tile size (px)
MW, MH = 270, 210           # map size (tiles)
GROUND, BUILDING, WATER, ROAD, GRASS = 0, 1, 2, 3, 4
START, HOME = (6, MH - 6), (MW - 8, 14)

SW, SH = CFG["screen"]["width"], CFG["screen"]["height"]
ZOOM = CFG["screen"]["zoom"]
VW, VH = int(SW / ZOOM), int(SH / ZOOM)
CAM_LAG, ACCEL = CFG["feel"]["camera_lag"], CFG["feel"]["accel"]
PLAYER = CFG["player"]
FOOT_COPS = CFG["police"]["foot_cops"]
BIKES_PER_ROAD = CFG["police"]["bikes_per_road"]
LAMP_R = CFG["night"]["lamp_radius"]
DARKNESS = CFG["night"]["darkness"]
SPRINT_SECS, REFILL_SECS = CFG["stamina"]["sprint_secs"], CFG["stamina"]["refill_secs"]
RECOVER_AT = CFG["stamina"]["recover_at"]
KNOCK_SECS, SHELTER_SECS = CFG["shelter"]["knock_secs"], CFG["shelter"]["shelter_secs"]
SHELTER_CHANCE = CFG["shelter"]["chance"]
# name: (detection radius, speed, uniform colour)
POLICE_TYPES = {k: (v["radius"], v["speed"], tuple(v["color"])) for k, v in CFG["police"]["types"].items()}

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
    """Rough Ouagadougou: barrages up north, a maze of 1-tile sector streets,
    big asphalt roads crossing it, Place des Nations Unies in the middle.
    Returns (grid, lamps, roads); each road is its list of centerline tiles."""
    rnd = random.Random(seed)
    g = [[BUILDING] * MW for _ in range(MH)]

    def dig(x, y, v=GROUND):
        if 0 <= x < MW and 0 <= y < MH:
            g[y][x] = v

    # sector streets: 1 tile wide every 6 tiles, some segments built over (dead ends)
    for y in range(MH):
        for x in range(MW):
            if x % 6 == 0 or y % 6 == 0:
                g[y][x] = GROUND
    for iy in range(0, MH, 6):
        for ix in range(0, MW, 6):
            for dx, dy in ((1, 0), (0, 1)):
                if rnd.random() < 0.25:
                    for i in range(1, 6):
                        dig(ix + dx * i, iy + dy * i, BUILDING)
    # courtyards / alleys / vacant lots
    for _ in range(2300):
        bx, by = rnd.randrange(MW), rnd.randrange(MH)
        for dx in range(rnd.randint(1, 3)):
            dig(bx + dx, by)
    # barrages (water) along the north
    for x in range(36, 234):
        for y in range(6 + int(6 * math.sin(x / 18)), 21 + int(6 * math.sin(x / 12 + 1))):
            dig(x, y, WATER)
    # big asphalt roads, 3 tiles wide; Charles de Gaulle crosses the barrage on the dam
    cx, cy = MW // 2, MH // 2
    roads = [
        [(x, cy) for x in range(MW)],                     # Av. Kwame Nkrumah
        [(x, 170) for x in range(MW)],                    # Bd des Tansoba
        [(cx, y) for y in range(MH)],                     # Bd Charles de Gaulle
        [(60, y) for y in range(28, MH)],                 # Av. Bassawarga
        [(cx + i, cy + i) for i in range(MH - cy - 2)],   # Av. Babanguida, to the south-east
    ]
    lamps = []
    for road in roads:
        (x0, y0), (x1, y1) = road[0], road[1]
        dx, dy = x1 - x0, y1 - y0
        for i, (x, y) in enumerate(road):
            for ox in (-1, 0, 1):
                for oy in (-1, 0, 1):
                    dig(x + ox, y + oy, ROAD)
            if i % 6 == 0:  # lamp post on the verge
                lamps.append(((x + 0.5 - dy * 1.6) * T, (y + 0.5 + dx * 1.6) * T))
    # roundabout: Place des Nations Unies, a garden ringed by asphalt
    for y in range(cy - 7, cy + 8):
        for x in range(cx - 7, cx + 8):
            d = math.hypot(x - cx, y - cy)
            if d <= 7:
                dig(x, y, GRASS if d <= 3.5 else ROAD)
    for a in range(0, 360, 45):
        lamps.append(((cx + 0.5 + math.cos(math.radians(a)) * 6) * T,
                      (cy + 0.5 + math.sin(math.radians(a)) * 6) * T))
    # maquis lights scattered at random intersections
    for _ in range(160):
        lamps.append((rnd.randrange(0, MW, 6) * T + T / 2, rnd.randrange(0, MH, 6) * T + T / 2))
    # make sure start and home are walkable
    for x, y in (START, HOME):
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                dig(x + dx, y + dy)
    y = HOME[1]
    while not bfs(g, START, HOME, MW * MH):  # built-over segments can wall home in: open a lane south
        y += 1
        dig(HOME[0], y)
    return g, lamps, roads


def find_doors(g, seed=7):
    """One door per building block, on a random wall face that touches the street."""
    rnd = random.Random(seed)
    seen, doors = set(), []
    for y in range(MH):
        for x in range(MW):
            if g[y][x] != BUILDING or (x, y) in seen:
                continue
            seen.add((x, y))
            stack, faces = [(x, y)], []
            while stack:
                cx, cy = stack.pop()
                for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    nx, ny = cx + dx, cy + dy
                    if not (0 <= nx < MW and 0 <= ny < MH):
                        continue
                    if g[ny][nx] == BUILDING and (nx, ny) not in seen:
                        seen.add((nx, ny))
                        stack.append((nx, ny))
                    elif g[ny][nx] in (GROUND, ROAD):
                        faces.append(((cx + 0.5 + dx * 0.5) * T, (cy + 0.5 + dy * 0.5) * T))
            if faces:
                doors.append(rnd.choice(faces))
    return doors


LABELS = [
    ("Barrage n°3", 60, 12), ("Barrage n°2", 120, 12), ("Barrage n°1", 186, 12),
    ("Place des Nations Unies", MW // 2 - 5, MH // 2 + 9), ("Av. Kwame Nkrumah", 20, MH // 2 - 3),
    ("Bd Charles de Gaulle", MW // 2 + 3, 150), ("Bd des Tansoba", 20, 167),
    ("Av. Bassawarga", 62, 60), ("Av. Babanguida", MW // 2 + 30, MH // 2 + 26),
    ("Gounghin", 15, 180), ("Marché Rood Woko", 90, 84), ("Ouaga 2000", 210, 192),
    ("Patte d'Oie", 45, 138), ("Tampouy", 30, 40), ("MAISON", HOME[0] - 2, HOME[1] - 2),
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


def step_stamina(stamina, exhausted, sprinting, dt):
    """Linear drain/refill on a 0..1 bar. Returns (stamina, exhausted)."""
    if sprinting:
        stamina = max(0.0, stamina - dt / SPRINT_SECS)
    else:
        stamina = min(1.0, stamina + dt / REFILL_SECS)
    if stamina == 0:
        exhausted = True
    elif stamina >= RECOVER_AT:
        exhausted = False
    return stamina, exhausted


def lerp(a, b, rate, dt):
    """Frame-rate independent ease of a towards b."""
    return a + (b - a) * (1 - math.exp(-rate * dt))


def move_with_collision(g, pos, vel, dt, rad):
    """Axis-separated movement; water is walkable but slow (wading)."""
    for axis in (0, 1):
        nxt = list(pos)
        nxt[axis] += vel[axis] * dt
        if not any(solid(g, nxt[0] + ox, nxt[1] + oy) for ox in (-rad, rad) for oy in (-rad, rad)):
            pos[axis] = nxt[axis]


# ---------------------------------------------------------------- actors
class Cop:
    def __init__(self, kind, pos, route=None):
        self.kind = kind
        self.route = route        # motorbikes ride this road's centerline back and forth
        self.leg = 1
        self.dir = (1, 0)
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
        elif not self.path and self.route:
            t = self.tile()
            if t in self.route:
                i = self.route.index(t)
                self.path = self.route[i + 1:] if self.leg > 0 else self.route[:i][::-1]
                if not self.path:
                    self.leg = -self.leg
            else:  # lost the chase: ride back to the asphalt
                back = min(self.route, key=lambda r: math.dist(r, t))
                self.path = bfs(g, t, back, MW * MH)
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
                self.dir = (dx, dy)
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


def draw_bar(surf, rect, frac, col):
    pygame.draw.rect(surf, (40, 40, 40), rect)
    pygame.draw.rect(surf, col, (rect[0], rect[1], rect[2] * frac, rect[3]))
    pygame.draw.rect(surf, (220, 220, 220), rect, 1)


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


# ---------------------------------------------------------------- dialogue
def asset(path):
    return Path(__file__).parent / path


def placeholder_face(name, size):
    """Mock portrait, stable per speaker name."""
    rnd = random.Random(name)
    s = pygame.Surface((size, size))
    s.fill(rnd.choice([(70, 60, 90), (60, 80, 70), (90, 70, 50), (50, 60, 90)]))
    skin = rnd.choice([(90, 55, 30), (110, 70, 40), (70, 45, 25)])
    pygame.draw.ellipse(s, rnd.choice([(200, 120, 40), (40, 120, 160), (160, 40, 60), (220, 200, 60)]),
                        (size * .1, size * .7, size * .8, size * .6))                      # boubou
    pygame.draw.circle(s, skin, (size // 2, size * .45), size * .27)                       # head
    for ex in (.4, .6):
        pygame.draw.circle(s, (240, 240, 240), (size * ex, size * .42), size * .05)
        pygame.draw.circle(s, (20, 20, 20), (size * ex, size * .43), size * .025)
    pygame.draw.arc(s, (30, 15, 10), (size * .4, size * .45, size * .2, size * .14), math.pi * 1.1, math.pi * 1.9, 2)
    if rnd.random() < .5:                                                                   # headscarf
        pygame.draw.ellipse(s, rnd.choice([(230, 140, 30), (40, 160, 90), (180, 40, 140)]),
                            (size * .2, size * .12, size * .6, size * .25))
    return s


class Dialogue:
    """Bottom-of-screen dialogue box that plays queued lines on its own (no key to advance).
    Look is driven by [dialogue] in config.toml: font, box texture, per-speaker portraits."""
    AVATAR = 104

    def __init__(self, cfg):
        self.cfg = cfg
        self.font = (pygame.font.Font(asset(cfg["font"]), cfg["font_size"]) if cfg["font"]
                     else pygame.font.SysFont("dejavusans", cfg["font_size"]))
        self.name_font = pygame.font.SysFont("dejavusans", cfg["font_size"], bold=True)
        self.texture = pygame.image.load(asset(cfg["box_texture"])).convert_alpha() if cfg["box_texture"] else None
        self.avatars = {}
        self.queue = deque()     # (speaker or None for narrator, text)
        self.t = 0.0             # time on the current line

    def say(self, speaker, lines):
        """Queue one line or a list of lines; a speaker of None is the narrator."""
        for text in [lines] if isinstance(lines, str) else lines:
            self.queue.append((speaker, text))

    @property
    def active(self):
        return bool(self.queue)

    def clear(self):
        self.queue.clear()
        self.t = 0.0

    def update(self, dt):
        if not self.queue:
            return
        self.t += dt
        if self.t > len(self.queue[0][1]) / self.cfg["chars_per_sec"] + self.cfg["hold_secs"]:
            self.queue.popleft()
            self.t = 0.0

    def avatar(self, speaker):
        if speaker not in self.avatars:
            path = CFG["dialogue"]["avatars"].get(speaker)
            img = pygame.image.load(asset(path)).convert_alpha() if path else placeholder_face(speaker, self.AVATAR)
            self.avatars[speaker] = pygame.transform.smoothscale(img, (self.AVATAR, self.AVATAR))
        return self.avatars[speaker]

    def wrap(self, text, width):
        lines, cur = [], ""
        for word in text.split():
            if cur and self.font.size(cur + " " + word)[0] > width:
                lines.append(cur)
                cur = word
            else:
                cur = (cur + " " + word).strip()
        return lines + [cur]

    def draw(self, surf):
        if not self.queue:
            return
        speaker, text = self.queue[0]
        box = pygame.Rect(16, surf.get_height() - 150, surf.get_width() - 32, 134)
        if self.texture:
            surf.blit(pygame.transform.smoothscale(self.texture, box.size), box)
        else:
            bg = pygame.Surface(box.size, pygame.SRCALPHA)
            bg.fill((10, 10, 20, 215))
            surf.blit(bg, box)
            pygame.draw.rect(surf, (230, 200, 140), box, 2, border_radius=6)
        x = box.x + 15
        if speaker:
            surf.blit(self.avatar(speaker), (x, box.y + 15))
            pygame.draw.rect(surf, (230, 200, 140), (x, box.y + 15, self.AVATAR, self.AVATAR), 2)
            x += self.AVATAR + 18
            surf.blit(self.name_font.render(speaker, True, (255, 220, 120)), (x, box.y + 12))
        shown = text[:int(self.t * self.cfg["chars_per_sec"])]   # typewriter
        y = box.y + (42 if speaker else 20)
        for line in self.wrap(shown, box.right - x - 15):
            surf.blit(self.font.render(line, True, (240, 240, 240) if speaker else (190, 190, 210)), (x, y))
            y += self.font.get_linesize()


# ---------------------------------------------------------------- game
class Game:
    def __init__(self):
        pygame.init()
        pygame.display.set_caption("Couvre-feu — Ouagadougou")
        self.screen = pygame.display.set_mode((SW, SH))
        self.view = pygame.Surface((VW, VH))
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("dejavusans", 16)
        self.big = pygame.font.SysFont("dejavusans", 42, bold=True)
        self.grid, self.lamps, self.roads = build_map()
        self.doors = find_doors(self.grid)
        rnd = random.Random(3)
        self.owners = [rnd.choice(CFG["dialogue"]["owner"]["names"]) for _ in self.doors]
        self.dialogue = Dialogue(CFG["dialogue"])
        self.prepare_world()
        self.reset()

    def prepare_world(self):
        """Per-tile colours, road dashes and label surfaces for draw_world.
        No prerendered world: at 270x210 tiles it would be a ~230 MB surface."""
        rnd = random.Random(1)
        colors = {GROUND: (150, 95, 60), WATER: (30, 60, 90), ROAD: (38, 38, 42), GRASS: (60, 110, 50)}
        self.tcol = []
        for row in self.grid:
            self.tcol.append([
                rnd.choice([(170, 150, 120), (190, 170, 140), (120, 110, 100), (160, 90, 70)]) if v == BUILDING
                else [max(0, min(255, k + rnd.randint(-8, 8))) for k in colors[v]]
                for v in row])
        self.dashes = {}             # centerline tile -> direction, every other tile
        for road in self.roads:
            (x0, y0), (x1, y1) = road[0], road[1]
            for x, y in road[::2]:
                if self.grid[y][x] == ROAD:
                    self.dashes[(x, y)] = (x1 - x0, y1 - y0)
        self.labels = []
        for text, x, y in LABELS:
            col = (255, 230, 120) if text == "MAISON" else (240, 240, 220)
            self.labels.append((self.font.render(text, True, col), (x * T, y * T)))

    def draw_world(self, cam):
        s = self.view
        for ty in range(max(0, cam[1] // T), min(MH, (cam[1] + VH) // T + 1)):
            for tx in range(max(0, cam[0] // T), min(MW, (cam[0] + VW) // T + 1)):
                x, y = tx * T - cam[0], ty * T - cam[1]
                s.fill(self.tcol[ty][tx], (x, y, T, T))
                if self.grid[ty][tx] == BUILDING:
                    pygame.draw.rect(s, (80, 70, 60), (x, y, T, T), 1)
                elif (tx, ty) in self.dashes:
                    dx, dy = self.dashes[(tx, ty)]
                    mx, my = x + T // 2, y + T // 2
                    pygame.draw.line(s, (220, 220, 200), (mx - dx * 9, my - dy * 9), (mx + dx * 9, my + dy * 9), 2)
        for lx, ly in self.lamps:
            pygame.draw.circle(s, (60, 60, 60), (int(lx - cam[0]), int(ly - cam[1])), 4)
        for dx, dy in self.doors:
            if -T < dx - cam[0] < VW + T and -T < dy - cam[1] < VH + T:
                pygame.draw.rect(s, (90, 50, 20), (dx - cam[0] - 6, dy - cam[1] - 6, 12, 12))
        for surf, (x, y) in self.labels:
            s.blit(surf, (x - cam[0], y - cam[1]))

    def reset(self):
        self.player = [START[0] * T + T / 2, START[1] * T + T / 2]
        self.vel = [0.0, 0.0]
        self.cam = list(self.player)  # view centre, eases after the player
        self.home = pygame.Rect((HOME[0] - 1) * T, (HOME[1] - 1) * T, 3 * T, 3 * T)
        rnd = random.Random()
        self.cops = []
        kinds = [k for k in POLICE_TYPES if k != "Motard"]
        while len(self.cops) < FOOT_COPS:
            x, y = rnd.randrange(MW), rnd.randrange(MH)
            if self.grid[y][x] == GROUND and math.dist((x, y), START) > 15:
                self.cops.append(Cop(rnd.choice(kinds), (x * T + T / 2, y * T + T / 2)))
        for road in self.roads:
            for _ in range(BIKES_PER_ROAD):
                x, y = rnd.choice(road[len(road) // 6:])   # not right on top of the start
                self.cops.append(Cop("Motard", (x * T + T / 2, y * T + T / 2), road))
        self.state = "play"
        self.t = 0.0                 # seconds since curfew (21:00)
        self.stamina, self.exhausted = 1.0, False
        self.death_t = 0.0
        self.caption = ""
        self.used = set()            # doors that already sheltered us
        self.knock, self.shelter = 0.0, 0.0
        self.sheltered_at = None     # door we're hiding behind
        self.dialogue.clear()

    # ---- update
    def player_lit(self):
        return any(math.dist(self.player, l) < LAMP_R * 0.8 for l in self.lamps)

    def near_door(self):
        return next((i for i, d in enumerate(self.doors) if math.dist(self.player, d) < 22), None)

    def owner_says(self, door, kind):
        self.dialogue.say(self.owners[door], random.choice(CFG["dialogue"]["owner"][kind]))

    def update(self, dt):
        self.cam = [lerp(c, p, CAM_LAG, dt) for c, p in zip(self.cam, self.player)]
        if self.state != "play":
            self.death_t += dt
            return
        self.t += dt
        self.dialogue.update(dt)
        if self.shelter > 0:  # inside: invisible and untouchable, catching your breath
            self.shelter -= dt
            self.stamina, self.exhausted = step_stamina(self.stamina, self.exhausted, False, dt)
            for c in self.cops:
                c.update(dt, self.grid, self.player, False)
            if self.shelter <= 0:
                self.owner_says(self.sheltered_at, "leave")
            return
        k = pygame.key.get_pressed()
        mx = (k[pygame.K_d] or k[pygame.K_RIGHT]) - (k[pygame.K_a] or k[pygame.K_LEFT])
        my = (k[pygame.K_s] or k[pygame.K_DOWN]) - (k[pygame.K_w] or k[pygame.K_UP])
        mods = pygame.key.get_mods()
        mode = "walk"
        sprinting = bool(mods & pygame.KMOD_SHIFT and (mx or my) and not self.exhausted)
        if mods & pygame.KMOD_CTRL:
            mode, sprinting = "sneak", False
        elif sprinting:
            mode = "sprint"
        elif self.exhausted:
            mode = "exhausted"
        speed, vis = PLAYER[mode]["speed"], PLAYER[mode]["vis"]
        self.stamina, self.exhausted = step_stamina(self.stamina, self.exhausted, sprinting, dt)
        if self.grid[int(self.player[1] // T)][int(self.player[0] // T)] == WATER:
            speed *= PLAYER["wading"]
        n = math.hypot(mx, my) or 1
        self.vel = [lerp(v, m / n * speed, ACCEL, dt) for v, m in zip(self.vel, (mx, my))]
        move_with_collision(self.grid, self.player, self.vel, dt, 7)
        if self.player_lit():
            vis *= PLAYER["lit_vis"]
        self.moving = bool(mx or my)

        door = self.near_door()
        if k[pygame.K_e] and door is not None and not self.moving:
            if door in self.used:
                if not self.dialogue.active:  # E is held: don't requeue every frame
                    self.dialogue.say(None, random.choice(CFG["dialogue"]["owner"]["used"]))
            else:
                self.knock += dt
                if self.knock >= KNOCK_SECS:
                    self.knock = 0.0
                    if random.random() < SHELTER_CHANCE:
                        self.shelter = SHELTER_SECS
                        self.used.add(door)
                        self.sheltered_at = door
                        self.dialogue.clear()
                        self.owner_says(door, "accept")
                    else:
                        self.dialogue.clear()
                        self.owner_says(door, "refuse")
        else:
            self.knock = 0.0

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
        cam = (int(self.cam[0] - VW / 2), int(self.cam[1] - VH / 2))
        if self.state == "dead" and self.death_t < 4:  # screen shake while beating
            cam = (cam[0] + random.randint(-4, 4), cam[1] + random.randint(-4, 4))
        s = self.view
        s.fill((0, 0, 0))
        self.draw_world(cam)

        def sp(p):
            return p[0] - cam[0], p[1] - cam[1]

        pygame.draw.rect(s, (255, 220, 80), self.home.move(-cam[0], -cam[1]), 2)
        for c in self.cops:
            x, y = sp(c.pos)
            if -T < x < VW + T and -T < y < VH + T:
                if c.route:
                    w, h = (30, 12) if abs(c.dir[0]) > abs(c.dir[1]) else (12, 30)
                    pygame.draw.ellipse(s, (15, 15, 15), (x - w / 2, y - h / 2, w, h))
                draw_person(s, (x, y), c.color)
        for i in self.used:
            x, y = sp(self.doors[i])
            pygame.draw.line(s, (20, 10, 5), (x - 5, y - 5), (x + 5, y + 5), 2)
            pygame.draw.line(s, (20, 10, 5), (x - 5, y + 5), (x + 5, y - 5), 2)
        if (self.state == "play" and self.shelter <= 0) or self.state == "home":
            draw_person(s, sp(self.player), (200, 40, 40))

        # night overlay with light holes
        dark = pygame.Surface((VW, VH), pygame.SRCALPHA)
        dark.fill((5, 5, 25, DARKNESS))
        for l in self.lamps:
            x, y = sp(l)
            if -LAMP_R < x < VW + LAMP_R and -LAMP_R < y < VH + LAMP_R:
                dark.blit(light_tex(LAMP_R), (x - LAMP_R, y - LAMP_R), special_flags=pygame.BLEND_RGBA_SUB)
        for c in self.cops:
            x, y = sp(c.pos)
            r = c.radius
            if -r < x < VW + r and -r < y < VH + r:
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

        if self.state == "play":
            if self.knock > 0:
                draw_bar(s, (px - 20, py - 22, 40, 6), self.knock / KNOCK_SECS, (230, 180, 60))
                if int(self.knock * 2) % 2:
                    s.blit(self.font.render("toc toc", True, (230, 200, 140)), (px + 12, py - 30))
            if self.shelter > 0:
                draw_bar(s, (px - 20, py - 22, 40, 6), self.shelter / SHELTER_SECS, (80, 160, 230))
        if self.state == "play" and self.exhausted and self.shelter <= 0:
            pant = "*huff* *puff*" if int(self.t * 3) % 2 else "*wheeze*"
            s.blit(self.font.render(pant, True, (220, 220, 255)), (px - 40, py - 34))

        if self.state == "dead":
            self.draw_death(sp(self.player))
        pygame.transform.smoothscale(s, (SW, SH), self.screen)
        if self.state == "dead" and self.death_t > 4.5:
            self.banner("ARRÊTÉ.", self.caption + "   [R] retry")
        elif self.state == "home":
            self.banner("Tu es rentré. Maman est quand même fâchée.", "R to play again")
        elif self.state == "play":
            self.dialogue.draw(self.screen)
        self.hud()
        pygame.display.flip()

    def draw_death(self, p):
        t, s = self.death_t, self.view
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
        self.screen.blit(self.font.render("WASD move · Ctrl sneak · Shift sprint · hold E at a door to knock", True, (160, 160, 160)), (12, 30))
        bar = pygame.Rect(12, 54, 200, 12)
        col = (200, 60, 60) if self.exhausted else (80, 200, 90)
        draw_bar(self.screen, bar, self.stamina, col)
        pygame.draw.line(self.screen, (220, 220, 220), (bar.x + bar.w * RECOVER_AT, bar.y),
                         (bar.x + bar.w * RECOVER_AT, bar.bottom - 1))

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
    g, _, roads = build_map()
    assert g[START[1]][START[0]] == GROUND and g[HOME[1]][HOME[0]] == GROUND
    assert bfs(g, START, HOME, limit=MW * MH), "home must be reachable from start"
    assert all(g[y][x] != BUILDING for road in roads for x, y in road), "bike routes must be rideable"
    st, ex = 1.0, False
    for _ in range(600):                      # 10 s of sprint at 60 fps empties the bar
        st, ex = step_stamina(st, ex, True, 1 / 60)
    assert st < 1e-9 and ex
    st, ex = step_stamina(st, ex, False, 15)  # 15 s rest = 25%: exhaustion ends
    assert abs(st - 0.25) < 1e-9 and not ex
    st, ex = step_stamina(st, ex, False, 100)
    assert st == 1.0                          # capped
    doors = find_doors(g)
    assert len(doors) > 50
    for x, y in doors:                        # each door sits between a wall and the street
        if x % T == 0:
            pair = {solid(g, x - 1, y), solid(g, x + 1, y)}
        else:
            pair = {solid(g, x, y - 1), solid(g, x, y + 1)}
        assert pair == {True, False}, (x, y)


if __name__ == "__main__":
    selfcheck()
    Game().run()
