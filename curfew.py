"""COUVRE-FEU — a top-down stealth game set in Ouagadougou after curfew.

Move: WASD/arrows   Sneak: Ctrl   Sprint: Shift   Knock: hold E at a door   Retry: R   Quit: Esc
"""
import math
import random
import tomllib
from collections import deque
from pathlib import Path

import pygame

import controls

CFG = tomllib.loads(Path(__file__).with_name("config.toml").read_text(encoding="utf-8"))

T = 32                      # tile size (px)
MW, MH = 270, 210           # map size (tiles)
GROUND, BUILDING, WATER, ROAD, GRASS, WALL, GATE, YARD = range(8)
SOLID = (BUILDING, WALL, GATE)
LOT = 5                     # lot pitch in tiles: 4x4 yard + one wall (walls are shared between neighbours)
LOTS_X, LOTS_Y = 3, 2       # lots per block; 1-tile streets run between blocks
BLOCK_X, BLOCK_Y = 2 + LOT * LOTS_X, 2 + LOT * LOTS_Y   # street + lots + closing wall
HUB = (0, MH // 2)           # west end of Av. Kwame Nkrumah: always open, every street connects to it

SW, SH = CFG["screen"]["width"], CFG["screen"]["height"]
ZOOM = CFG["screen"]["zoom"]
VW, VH = int(SW / ZOOM), int(SH / ZOOM)
CAM_LAG, ACCEL = CFG["feel"]["camera_lag"], CFG["feel"]["accel"]
PLAYER = CFG["player"]
FOOT_COPS = CFG["police"]["foot_cops"]
BIKES_PER_ROAD = CFG["police"]["bikes_per_road"]
NIGHT = CFG["night"]
LAMP_R = NIGHT["lamp_radius"]
DARKNESS = CFG["night"]["darkness"]
SPRINT_SECS, REFILL_SECS = CFG["stamina"]["sprint_secs"], CFG["stamina"]["refill_secs"]
RECOVER_AT = CFG["stamina"]["recover_at"]
KNOCK_SECS, SHELTER_SECS = CFG["shelter"]["knock_secs"], CFG["shelter"]["shelter_secs"]
SHELTER_CHANCE = CFG["shelter"]["chance"]
BINS, ITEMS, WALLS, LOTS = CFG["bins"], CFG["items"], CFG["walls"], CFG["lots"]
EFFECTS, TRIP, DJINNS = CFG["effects"], CFG["trip"], CFG["djinns"]
# name: (sight radius, torch radius, speed, uniform colour)
POSTED = CFG["police"]["posted"]
POLICE_TYPES = {k: (v["radius"], v["light"], v["speed"], tuple(v["color"])) for k, v in CFG["police"]["types"].items()}

DEATH_CAPTIONS = [
    "Rapport officiel : le suspect a glissé sur une peau de banane. 14 fois.",
    "Cause du décès : rébellion par respiration.",
    "La matraque a été proposée pour une médaille d'honneur.",
    "Il voulait juste acheter du pain. Le pain est interdit après 21h.",
    "Autopsie : mort naturelle. À Ouaga, la police, c'est naturel.",
    "La famille recevra la facture de la matraque cassée.",
]


# ---------------------------------------------------------------- map
def build_map(seed=226):
    """Rough Ouagadougou: barrages up north, a grid of 1-tile laterite streets between blocks of walled
    parcelles (yard, house in the middle, gate on the street), big asphalt roads crossing it,
    Place des Nations Unies in the middle.
    Returns (grid, lamps, roads, walls, gates):
      roads: each road's centerline tiles
      walls: wall tile -> kind (how it looks: concrete, banco or glass-topped)
      gates: (gate tile, street tile in front of it), one per lot"""
    rnd = random.Random(seed)
    g = [[GROUND] * MW for _ in range(MH)]

    def dig(x, y, v=GROUND):
        if 0 <= x < MW and 0 <= y < MH:
            g[y][x] = v

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
    # maquis lights at random street crossings
    for _ in range(160):
        lamps.append((rnd.randrange(0, MW, BLOCK_X) * T + T / 2, rnd.randrange(0, MH, BLOCK_Y) * T + T / 2))

    # parcelles: lots share their side walls; a lot only goes where its whole footprint is open laterite
    kinds = list(WALLS["kinds"])
    weights = [WALLS["kinds"][k]["weight"] for k in kinds]
    walls, gates = {}, []
    for by in range(0, MH, BLOCK_Y):
        for bx in range(0, MW, BLOCK_X):
            for j in range(LOTS_Y):
                for i in range(LOTS_X):
                    x0, y0 = bx + 1 + i * LOT, by + 1 + j * LOT
                    x1, y1 = x0 + LOT, y0 + LOT              # walls included
                    if x1 >= MW or y1 >= MH or rnd.random() < LOTS["vacant"]:
                        continue
                    foot = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
                    if any(g[y][x] not in (GROUND, WALL) for x, y in foot):
                        continue
                    kind = rnd.choices(kinds, weights)[0]
                    for x, y in foot:
                        edge_x, edge_y = x in (x0, x1), y in (y0, y1)
                        if edge_x and edge_y:                # corner post
                            g[y][x] = WALL
                            walls[(x, y)] = kind
                        elif edge_x or edge_y:
                            g[y][x] = WALL
                            walls[(x, y)] = kind
                        else:
                            g[y][x] = YARD
                    # the house sits in the middle of the yard, never touching the walls
                    w, h = (2, 2) if rnd.random() >= LOTS["small_house"] else rnd.choice([(2, 1), (1, 2)])
                    hx, hy = x0 + 2 + rnd.randint(0, 2 - w), y0 + 2 + rnd.randint(0, 2 - h)
                    for y in range(hy, hy + h):
                        for x in range(hx, hx + w):
                            g[y][x] = BUILDING
                    # gate on a side facing the street (outer sides of the block are never shared)
                    sides = ([(x, y0, (x, y0 - 1)) for x in range(x0 + 1, x1)] if j == 0 else []) + \
                            ([(x, y1, (x, y1 + 1)) for x in range(x0 + 1, x1)] if j == LOTS_Y - 1 else []) + \
                            ([(x0, y, (x0 - 1, y)) for y in range(y0 + 1, y1)] if i == 0 else []) + \
                            ([(x1, y, (x1 + 1, y)) for y in range(y0 + 1, y1)] if i == LOTS_X - 1 else [])
                    x, y, out = rnd.choice(sides)
                    g[y][x] = GATE
                    walls.pop((x, y))
                    gates.append(((x, y), out))

    # open ground nobody can walk to (sealed between walls) becomes yard, so no cop or bin ends up stranded
    reach = flood(g, HUB)
    for y in range(MH):
        for x in range(MW):
            if g[y][x] == GROUND and (x, y) not in reach:
                g[y][x] = YARD
    gates = [(t, out) for t, out in gates if out in reach]   # (the others stay closed metal, see draw)
    return g, lamps, roads, walls, gates


def find_houses(g):
    """Connected building tiles: each one is a house."""
    seen, houses = set(), []
    for y in range(MH):
        for x in range(MW):
            if g[y][x] != BUILDING or (x, y) in seen:
                continue
            seen.add((x, y))
            stack, house = [(x, y)], []
            while stack:
                cx, cy = stack.pop()
                house.append((cx, cy))
                for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                    if 0 <= nx < MW and 0 <= ny < MH and g[ny][nx] == BUILDING and (nx, ny) not in seen:
                        seen.add((nx, ny))
                        stack.append((nx, ny))
            houses.append(house)
    return houses


def gate_door(gate, out):
    """Knocking spot: the middle of the gate's street-side edge."""
    return (gate[0] + out[0] + 1) * T / 2, (gate[1] + out[1] + 1) * T / 2


def find_bin_spots(g, reach):
    """Reachable street tiles against a lot wall; the bin is pushed up to the wall so it never blocks the street."""
    spots = []
    for x, y in reach:
        if g[y][x] != GROUND:
            continue
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if 0 <= x + dx < MW and 0 <= y + dy < MH and g[y + dy][x + dx] == WALL:
                spots.append(((x + 0.5 + dx * 0.3) * T, (y + 0.5 + dy * 0.3) * T))
                break
    return sorted(spots)                     # stable order, so a seeded sample is reproducible


LABELS = [
    ("Barrage n°3", 60, 12), ("Barrage n°2", 120, 12), ("Barrage n°1", 186, 12),
    ("Place des Nations Unies", MW // 2 - 5, MH // 2 + 9), ("Av. Kwame Nkrumah", 20, MH // 2 - 3),
    ("Bd Charles de Gaulle", MW // 2 + 3, 150), ("Bd des Tansoba", 20, 167),
    ("Av. Bassawarga", 62, 60), ("Av. Babanguida", MW // 2 + 30, MH // 2 + 26),
    ("Gounghin", 15, 180), ("Marché Rood Woko", 90, 84), ("Ouaga 2000", 210, 192),
    ("Patte d'Oie", 45, 138), ("Tampouy", 30, 40),
]


def solid(g, px, py):
    tx, ty = int(px // T), int(py // T)
    return not (0 <= tx < MW and 0 <= ty < MH) or g[ty][tx] in SOLID


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
            if n not in prev and 0 <= n[0] < MW and 0 <= n[1] < MH and g[n[1]][n[0]] not in SOLID:
                prev[n] = cur
                q.append(n)
    return []


def flood(g, start):
    """Every walkable tile reachable from start."""
    seen = {start}
    q = deque([start])
    while q:
        x, y = q.popleft()
        for n in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if n not in seen and 0 <= n[0] < MW and 0 <= n[1] < MH and g[n[1]][n[0]] not in SOLID:
                seen.add(n)
                q.append(n)
    return seen


def step_stamina(stamina, exhausted, sprinting, dt, refill_mult=1.0, bonus=0.0):
    """Linear drain/refill on a 0..1 bar; bonus is a per-second gain on top. Returns (stamina, exhausted)."""
    rate = -1 / SPRINT_SECS if sprinting else refill_mult / REFILL_SECS
    stamina = min(1.0, max(0.0, stamina + (rate + bonus) * dt))
    if stamina == 0:
        exhausted = True
    elif stamina >= RECOVER_AT:
        exhausted = False
    return stamina, exhausted


def lerp(a, b, rate, dt):
    """Frame-rate independent ease of a towards b."""
    return a + (b - a) * (1 - math.exp(-rate * dt))


def effect_mods(active):
    """Combined traits of the active [effect, seconds left] list: multipliers multiply, gains add."""
    m = {"refill": 1.0, "bonus": 0.0, "speed": 1.0, "sprint": 1.0, "invisible": False}
    for name, _ in active:
        e = EFFECTS[name]
        m["refill"] *= e.get("refill_mult", 1.0)
        m["bonus"] += e.get("stamina_per_sec", 0.0)
        m["speed"] *= e.get("speed_mult", 1.0)
        m["sprint"] *= e.get("sprint_mult", 1.0)
        m["invisible"] |= e.get("invisible", False)
    return m


def pick_trip(rnd, streets, gates):
    """A random start street tile and home gate index, a similar long way apart every run."""
    while True:
        start = rnd.choice(streets)
        far = [i for i, (t, _) in enumerate(gates) if TRIP["min_dist"] <= math.dist(start, t) <= TRIP["max_dist"]]
        if far:
            return start, rnd.choice(far)


def move_with_collision(g, pos, vel, dt, rad):
    """Axis-separated movement; water is walkable but slow (wading)."""
    for axis in (0, 1):
        nxt = list(pos)
        nxt[axis] += vel[axis] * dt
        if not any(solid(g, nxt[0] + ox, nxt[1] + oy) for ox in (-rad, rad) for oy in (-rad, rad)):
            pos[axis] = nxt[axis]


# ---------------------------------------------------------------- actors
class Cop:
    def __init__(self, kind, pos, route=None, post=None, lines=()):
        self.kind = kind
        self.route = route        # motorbikes ride this road's centerline back and forth
        self.post = post          # tile a posted cop stands on until he spots you
        self.lines = lines        # what he mutters there, overheard up close
        self.leg = 1
        self.dir = (1, 0)
        self.radius, self.light, self.speed, self.color = POLICE_TYPES[kind]
        self.pos = list(pos)
        self.path = []
        self.alert = 0.0          # 0..1, chase at 1
        self.chasing = False
        self.lost_for = 0.0
        self.repath = 0.0

    def give_up(self, g, away):
        """Lose track of the player at once; foot cops walk off away from the `away` tile
        (bikes just ride back to their road)."""
        self.alert, self.chasing, self.lost_for, self.path = 0.0, False, 0.0, []
        if self.route:
            return
        tx, ty = self.tile()
        a = math.atan2(ty - away[1], tx - away[0]) if (tx, ty) != tuple(away) else random.uniform(0, 2 * math.pi)
        for r in (14, 10, 6):                # first open street tile along that heading
            for da in (0, .4, -.4, .8, -.8):
                gx, gy = int(tx + math.cos(a + da) * r), int(ty + math.sin(a + da) * r)
                if 0 <= gx < MW and 0 <= gy < MH and g[gy][gx] == GROUND:
                    self.path = bfs(g, (tx, ty), (gx, gy), 2000)
                    if self.path:
                        return

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
        elif not self.path and self.post:
            if self.tile() != self.post:          # back to his post after a chase
                self.path = bfs(g, self.tile(), self.post, 2000)
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


def glow_tex(r, color):
    """Coloured radial glow, added on top of the darkness so each light source has its own tint."""
    key = (r, tuple(color))
    if key not in _light_cache:
        s = pygame.Surface((r * 2, r * 2))
        for i in range(r, 0, -2):
            f = 0.22 * (1 - (i / r) ** 2)
            pygame.draw.circle(s, [int(k * f) for k in color], (r, r), i)
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


def draw_wall(surf, x, y, kind, arms, tex=None):
    """A wall tile as a thick band from its centre towards each neighbouring wall/gate (arms = directions).
    kind: "high" concrete, "low" banco bricks, "glass" banco topped with broken bottles and spikes.
    With a texture, the bands are cut out of it instead of drawn."""
    banco = kind != "high"
    h = 14 if banco else 18
    c = T // 2
    rects = [pygame.Rect(x + c - h // 2, y + c - h // 2, h, h)]
    for dx, dy in arms:
        if dx:
            rects.append(pygame.Rect(x + (c if dx > 0 else 0), y + c - h // 2, c, h))
        else:
            rects.append(pygame.Rect(x + c - h // 2, y + (c if dy > 0 else 0), h, c))
    if tex:                                          # (horizontal, vertical) textures
        for i, r in enumerate(rects):
            vertical = i > 0 and not arms[i - 1][0]
            surf.blit(tex[vertical], r, area=r.move(-x, -y))
        return
    for r in rects:
        if banco:
            pygame.draw.rect(surf, (165, 120, 80), r)
        else:
            pygame.draw.rect(surf, (125, 125, 130), r)
    if banco:                                        # mortar lines
        for dx, dy in arms:
            for i in range(4, c, 7):
                if dx:
                    px = x + c + dx * i
                    pygame.draw.line(surf, (120, 85, 55), (px, y + c - h // 2), (px, y + c + h // 2 - 1))
                else:
                    py = y + c + dy * i
                    pygame.draw.line(surf, (120, 85, 55), (x + c - h // 2, py), (x + c + h // 2 - 1, py))
    else:                                            # lit top edge
        for r in rects:
            pygame.draw.line(surf, (170, 170, 175), r.topleft, r.topright)
    if kind == "glass":                              # broken bottles and spikes along the top
        for n, (dx, dy) in enumerate(arms):
            for i in range(3, c, 6):
                col = ((90, 190, 120), (200, 230, 240), (160, 95, 40))[(i // 6 + n) % 3]
                px, py = x + c + dx * i, y + c + dy * i
                if dx:
                    pygame.draw.polygon(surf, col, [(px - 2, py + 2), (px, py - 6), (px + 2, py + 2)])
                else:
                    pygame.draw.polygon(surf, col, [(px - 2, py + 2), (px, py - 6), (px + 2, py + 2)])
            pygame.draw.line(surf, (40, 40, 45), (x + c, y + c), (x + c + dx * c, y + c + dy * c), 1)


def draw_gate(surf, x, y, across, used, tex=None):
    """Metal portail in the wall line; across = the wall runs east-west. tex: the texture dict."""
    if tex and "gate" in tex:
        surf.blit(tex["gate" + ("" if across else "_v") + ("_dim" if used else "")], (x, y))
        return
    h = 16
    r = pygame.Rect(x, y + (T - h) // 2, T, h) if across else pygame.Rect(x + (T - h) // 2, y, h, T)
    pygame.draw.rect(surf, (45, 60, 75) if used else (40, 85, 110), r)
    for i in range(4, T, 5):                         # bars
        if across:
            pygame.draw.line(surf, (20, 30, 40), (x + i, r.top), (x + i, r.bottom - 1))
        else:
            pygame.draw.line(surf, (20, 30, 40), (r.left, y + i), (r.right - 1, y + i))
    pygame.draw.rect(surf, (15, 20, 25), r, 1)


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


# ---------------------------------------------------------------- textures
TILE_TEX = {GROUND: "ground", YARD: "yard", ROAD: "road", WATER: "water", GRASS: "grass",
            WALL: "ground", GATE: "ground"}   # walls and gates stand on the street texture
SPRITE_SIZE = {"bin": (14, 16), "lamp": (12, 12), "player": (24, 24), "cop": (24, 24), "cop_Motard": (26, 36),
               "djinn": (26, 40), "djinn_eyes": (26, 40), "portrait": (128, 128)}


def square_crop(img):
    """The centred square of a picture, whatever its shape (portraits)."""
    w, h = img.get_size()
    side = min(w, h)
    return img.subsurface(((w - side) // 2, (h - side) // 2, side, side))


def load_textures(paths):
    """name -> Surface for every texture the config names, scaled once.
    Sprites (SPRITE_SIZE) are fitted inside their box, keeping their shape. Tiles are scaled to TxT; a tile image
    that is a strip of square cells (e.g. 9 of them side by side) gives variants, listed under "<name>*".
    Also adds darker "bin_dim" / "gate_dim", rotated "gate_v" and "wall_<kind>_v" for north-south runs."""
    tex = {}
    for name, path in paths.items():
        if not path:
            continue
        img = pygame.image.load(asset(path)).convert_alpha()
        w, h = img.get_size()
        if name == "portrait":
            img = square_crop(img)
            w = h = img.get_width()
        box = SPRITE_SIZE.get(name) or SPRITE_SIZE.get("cop" if name.startswith("cop_") else name)
        if box:
            k = min(box[0] / w, box[1] / h)
            tex[name] = pygame.transform.smoothscale(img, (max(1, round(w * k)), max(1, round(h * k))))
        elif w >= 2 * h and w % h == 0:
            tex[name + "*"] = [pygame.transform.smoothscale(img.subsurface((i * h, 0, h, h)), (T, T)) for i in range(w // h)]
            tex[name] = tex[name + "*"][0]
        else:
            tex[name] = pygame.transform.smoothscale(img, (T, T))
    for name in [n for n in tex if n.startswith("wall_")]:
        tex[name + "_v"] = pygame.transform.rotate(tex[name], 90)
    for name in ("bin", "gate"):
        if name in tex:
            tex[name + "_dim"] = tex[name].copy()
            tex[name + "_dim"].fill((130, 130, 130), special_flags=pygame.BLEND_RGB_MULT)
    if "gate" in tex:
        tex["gate_v"] = pygame.transform.rotate(tex["gate"], 90)
        tex["gate_v_dim"] = pygame.transform.rotate(tex["gate_dim"], 90)
    return tex


def blit_center(surf, img, pos):
    surf.blit(img, img.get_rect(center=(int(pos[0]), int(pos[1]))))


_turned = {}


def facing(img, d):
    """img (drawn facing up) turned towards direction d, in 8 steps, cached."""
    k = round(-math.degrees(math.atan2(d[0], -d[1])) / 45) % 8 if any(d) else 0
    if (id(img), k) not in _turned:
        _turned[(id(img), k)] = pygame.transform.rotate(img, k * 45)
    return _turned[(id(img), k)]


def variant(cells, tx, ty):
    """A stable pseudo-random pick per tile, so variants don't line up in stripes."""
    return cells[((tx * 73856093) ^ (ty * 19349663)) % len(cells)]


# ---------------------------------------------------------------- dialogue
def asset(path):
    return Path(__file__).parent / path


def draw_djinn(surf, pos, t):
    """A tall smoky shadow, bobbing; all djinns look alike. Its eyes are drawn after the darkness (djinn_eyes)."""
    x, y = pos[0], pos[1] + math.sin(t * 2) * 2
    for i, (w, h, a) in enumerate(((22, 34, 70), (16, 28, 120), (10, 20, 170))):
        blob = pygame.Surface((w, h), pygame.SRCALPHA)
        pygame.draw.ellipse(blob, (30, 10, 45, a), (0, 0, w, h))
        surf.blit(blob, (x - w / 2, y - h + 8 - i * 2))
    for i in range(3):                               # smoke trailing at the feet
        pygame.draw.circle(surf, (40, 20, 55), (x - 6 + i * 6, y + 8 + math.sin(t * 5 + i) * 2), 3)


def djinn_eyes(surf, pos, t, at=(0, -16), spread=3, color=(255, 230, 120)):
    """Two glowing dots, `at` from the djinn's centre; drawn after the darkness so they show at night."""
    x, y = pos[0] + at[0], pos[1] + at[1] + math.sin(t * 2) * 2
    if int(t * 7) % 23:                              # blinks now and then
        for ex in (-spread, spread):
            pygame.draw.circle(surf, color, (x + ex, y), 2)


def djinn_portrait(size):
    s = pygame.Surface((size, size))
    s.fill((15, 10, 25))
    pygame.draw.ellipse(s, (40, 15, 60), (size * .2, size * .1, size * .6, size))
    for ex in (.4, .6):
        pygame.draw.circle(s, (255, 230, 120), (size * ex, size * .38), size * .05)
    return s


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
            faces = self.cfg["default_avatars"]
            path = CFG["dialogue"]["avatars"].get(speaker) or (random.Random(speaker).choice(faces) if faces else "")
            img = square_crop(pygame.image.load(asset(path)).convert_alpha()) if path else placeholder_face(speaker, self.AVATAR)
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
            pygame.draw.rect(surf, (92, 80, 70), (x, box.y + 15, self.AVATAR, self.AVATAR))   # so dark faces still read
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
        self.tex = load_textures(CFG["textures"])
        self.djinn_eye = {}          # where a textured djinn's own eyes are (shrunk, they'd be a faint pixel or two)
        if "djinn_eyes" in self.tex:
            img = self.tex["djinn_eyes"]
            r = img.get_bounding_rect(min_alpha=1)
            px = [img.get_at((x, y)) for x in range(r.left, r.right) for y in range(r.top, r.bottom) if img.get_at((x, y)).a]
            self.djinn_eye = {"at": (r.centerx - img.get_width() / 2, r.centery - img.get_height() / 2 - 8),
                              "spread": max(2, r.width // 2 - 1),
                              "color": [k * 255 // max(max(c[:3]) for c in px) for k in max(px, key=lambda c: sum(c[:3]))[:3]]}
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("dejavusans", 16)
        self.big = pygame.font.SysFont("dejavusans", 42, bold=True)
        self.grid, self.lamps, self.roads, self.walls, self.gates = build_map()
        houses = find_houses(self.grid)
        reach = flood(self.grid, HUB)
        self.bin_spots = find_bin_spots(self.grid, reach)
        self.streets = sorted(t for t in reach if self.grid[t[1]][t[0]] == GROUND)
        self.doors = [gate_door(t, out) for t, out in self.gates]   # you knock at the lot's gate
        self.gate_at = {t: i for i, (t, _) in enumerate(self.gates)}
        rnd = random.Random(3)
        self.owners = [rnd.choice(CFG["dialogue"]["owner"]["names"]) for _ in self.doors]
        self.dialogue = Dialogue(CFG["dialogue"])
        self.controls = controls.Controls((SW, SH), CFG["controls"], self.font)
        self.item_rects = []         # HUD inventory lines, so a tap on one uses that item
        for name in DJINNS["names"]:     # djinns get a shadow portrait unless the config gives one
            if name not in CFG["dialogue"]["avatars"]:
                self.dialogue.avatars[name] = djinn_portrait(Dialogue.AVATAR)
        self.prepare_world(houses)
        self.reset()

    def prepare_world(self, houses):
        """Per-tile colours, house outlines, road dashes and label surfaces for draw_world.
        No prerendered world: at 270x210 tiles it would be a ~230 MB surface."""
        rnd = random.Random(1)
        colors = {GROUND: (150, 95, 60), WATER: (30, 60, 90), ROAD: (38, 38, 42), GRASS: (60, 110, 50),
                  WALL: (150, 95, 60), GATE: (150, 95, 60), YARD: (178, 138, 92)}   # yards are swept clean
        def shade(c, d):         # brightness-only jitter, so ground doesn't turn into a checkerboard
            return [max(0, min(255, k + d)) for k in c]
        self.tcol = [[shade(colors[v], rnd.randint(-5, 5)) if v != BUILDING else None for v in row] for row in self.grid]
        # one flat colour per house (banco, whitewash, tin roof, terracotta...), outlined on its perimeter only
        palette = [(176, 128, 80), (200, 185, 150), (130, 130, 135), (165, 85, 60), (150, 165, 175), (190, 160, 110)]
        self.house_tex = {}          # house colour -> tinted roof textures (one per variant)
        for col in palette:
            for cell in self.tex.get("house*", [self.tex["house"]] if "house" in self.tex else []):
                tinted = cell.copy()
                tinted.fill(col, special_flags=pygame.BLEND_RGB_MULT)
                self.house_tex.setdefault(col, []).append(tinted)
        self.outlines = {}           # building tile -> outline segments, tile-relative
        owner = {t: i for i, house in enumerate(houses) for t in house}
        for i, house in enumerate(houses):
            col = rnd.choice(palette)
            for x, y in house:
                self.tcol[y][x] = col
                segs = [seg for (nx, ny), seg in (((x, y - 1), (0, 0, T - 1, 0)), ((x, y + 1), (0, T - 1, T - 1, T - 1)),
                                                  ((x - 1, y), (0, 0, 0, T - 1)), ((x + 1, y), (T - 1, 0, T - 1, T - 1)))
                        if owner.get((nx, ny)) != i]
                if segs:
                    self.outlines[(x, y)] = segs
        self.dashes = {}             # centerline tile -> direction, every other tile
        for road in self.roads:
            (x0, y0), (x1, y1) = road[0], road[1]
            for x, y in road[::2]:
                if self.grid[y][x] == ROAD:
                    self.dashes[(x, y)] = (x1 - x0, y1 - y0)
        # wall bands reach towards neighbouring walls and gates, so corners and junctions join up
        self.wall_arms = {}
        for (x, y) in self.walls:
            self.wall_arms[(x, y)] = [(dx, dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                                      if 0 <= x + dx < MW and 0 <= y + dy < MH and self.grid[y + dy][x + dx] in (WALL, GATE)]
        self.labels = []
        for text, x, y in LABELS:
            self.labels.append((self.font.render(text, True, (240, 240, 220)), (x * T, y * T)))

    def draw_world(self, cam):
        s = self.view
        for ty in range(max(0, cam[1] // T), min(MH, (cam[1] + VH) // T + 1)):
            for tx in range(max(0, cam[0] // T), min(MW, (cam[0] + VW) // T + 1)):
                x, y = tx * T - cam[0], ty * T - cam[1]
                s.fill(self.tcol[ty][tx], (x, y, T, T))   # under any texture, so transparent ones still read
                v = self.grid[ty][tx]
                key = TILE_TEX.get(v)
                cells = self.house_tex.get(tuple(self.tcol[ty][tx])) if v == BUILDING else self.tex.get(f"{key}*")
                tile = variant(cells, tx, ty) if cells else self.tex.get(key)
                if tile:
                    s.blit(tile, (x, y))
                if (tx, ty) in self.wall_arms:
                    kind = self.walls[(tx, ty)]
                    tex = self.tex.get("wall_" + kind)
                    draw_wall(s, x, y, kind, self.wall_arms[(tx, ty)], tex and (tex, self.tex["wall_" + kind + "_v"]))
                elif self.grid[ty][tx] == GATE:
                    i = self.gate_at.get((tx, ty))
                    left = 0 < tx and self.grid[ty][tx - 1] == WALL
                    draw_gate(s, x, y, left or (tx + 1 < MW and self.grid[ty][tx + 1] == WALL), i is None or i in self.used, self.tex)
                elif (tx, ty) in self.outlines:
                    for x1, y1, x2, y2 in self.outlines[(tx, ty)]:
                        pygame.draw.line(s, (60, 45, 35), (x + x1, y + y1), (x + x2, y + y2), 2)
                elif (tx, ty) in self.dashes:
                    dx, dy = self.dashes[(tx, ty)]
                    mx, my = x + T // 2, y + T // 2
                    pygame.draw.line(s, (220, 220, 200), (mx - dx * 9, my - dy * 9), (mx + dx * 9, my + dy * 9), 2)
        for lx, ly in self.lamps:
            if "lamp" in self.tex:
                blit_center(s, self.tex["lamp"], (lx - cam[0], ly - cam[1]))
            else:
                pygame.draw.circle(s, (60, 60, 60), (int(lx - cam[0]), int(ly - cam[1])), 4)
        for surf, (x, y) in self.labels:
            s.blit(surf, (x - cam[0], y - cam[1]))

    def reset(self):
        rnd = random.Random()
        start, self.home = pick_trip(rnd, self.streets, self.gates)   # home: index of our gate, never drawn
        self.player = [start[0] * T + T / 2, start[1] * T + T / 2]
        self.trip = math.dist(self.player, self.doors[self.home])
        self.pulse = 0.0             # phase of the HUD home beacon
        self.vel = [0.0, 0.0]
        self.facing = (0, -1)        # last direction moved, for the player sprite
        self.cam = list(self.player)  # view centre, eases after the player
        self.cops = []
        kinds = [k for k in POLICE_TYPES if k != "Motard"]
        while len(self.cops) < FOOT_COPS:
            x, y = rnd.randrange(MW), rnd.randrange(MH)
            if self.grid[y][x] == GROUND and math.dist((x, y), start) > 15:
                post = (x, y) if rnd.random() < POSTED["share"] else None
                lines = rnd.sample(POSTED["lines"], len(POSTED["lines"])) if post and rnd.random() < POSTED["chatty"] else ()
                cop = Cop(rnd.choice(kinds), (x * T + T / 2, y * T + T / 2), post=post, lines=lines)
                if post:          # facing a random way, so his torch shows where he's looking
                    a = rnd.uniform(0, 2 * math.pi)
                    cop.dir = (math.cos(a), math.sin(a))
                self.cops.append(cop)
        for road in self.roads:
            for _ in range(BIKES_PER_ROAD):
                x, y = rnd.choice([t for t in road if math.dist(t, start) > 15])
                self.cops.append(Cop("Motard", (x * T + T / 2, y * T + T / 2), road))
        self.state = "play"
        self.t = 0.0                 # seconds since curfew (21:00)
        self.stamina, self.exhausted = 1.0, False
        self.effects = []            # active [effect, seconds left]
        self.death_t = 0.0
        self.caption = ""
        self.used = set()            # doors that already sheltered us
        self.bins = rnd.sample(self.bin_spots, BINS["count"])
        spots = [t for t in self.streets if math.dist(t, start) > 20]
        self.djinns = [[((x + .5) * T, (y + .5) * T), rnd.choice(DJINNS["names"])]
                       for x, y in rnd.sample(spots, DJINNS["count"])]
        self.searched = set()        # bin indices already rummaged through
        self.inventory = {}          # item name -> count, in pickup order (slot 1, 2, ...)
        self.action = ("door", KNOCK_SECS)   # what the hold-E bar is filling for
        self.knock, self.shelter = 0.0, 0.0
        self.knock_lock = False      # set when a knock resolves, cleared when E is released
        self.vis = 1.0               # current detection multiplier, for the sight rings
        self.sheltered_at = None     # door we're hiding behind
        self.dialogue.clear()

    # ---- update
    def player_lit(self):
        return any(math.dist(self.player, l) < LAMP_R * 0.8 for l in self.lamps)

    def near_bin(self):
        return next((i for i, b in enumerate(self.bins)
                     if i not in self.searched and math.dist(self.player, b) < 22), None)

    def search_bin(self, i):
        self.searched.add(i)
        self.dialogue.clear()
        if random.random() < BINS["chance"]:
            name = random.choices(list(ITEMS), [v["weight"] for v in ITEMS.values()])[0]
            self.inventory[name] = self.inventory.get(name, 0) + 1
            self.dialogue.say(None, BINS["found"].format(item=name))
        else:
            self.dialogue.say(None, random.choice(BINS["empty"]))

    def use_item(self, slot):
        if slot >= len(self.inventory):
            return
        name = list(self.inventory)[slot]
        item = ITEMS[name]
        self.effects += [[effect, secs] for effect, secs in item["effects"]]
        self.inventory[name] -= 1
        if not self.inventory[name]:
            del self.inventory[name]
        if "use" in item:
            self.dialogue.clear()
            self.dialogue.say(None, item["use"])

    def near_djinn(self):
        return next((i for i, (p, _) in enumerate(self.djinns) if math.dist(self.player, p) < 26), None)

    def talk_to_djinn(self, i):
        """A gamble: gift or curse, nothing on the djinn tells which. Then it's gone."""
        _, name = self.djinns.pop(i)
        good = random.random() < DJINNS["generous"]
        gift = random.choice(DJINNS["gifts" if good else "curses"])
        self.dialogue.clear()
        self.dialogue.say(name, random.choice(DJINNS["gift_lines" if good else "curse_lines"]))
        if isinstance(gift, str):    # an item for the inventory, used when you choose
            self.inventory[gift] = self.inventory.get(gift, 0) + 1
            self.dialogue.say(None, DJINNS["gift_item"].format(item=gift))
        else:
            self.effects.append(list(gift))
        self.dialogue.say(None, DJINNS["vanish"])

    def near_door(self):
        return next((i for i, d in enumerate(self.doors) if math.dist(self.player, d) < 22), None)

    def leave_shelter(self):
        self.shelter = 0.0
        self.dialogue.clear()
        self.owner_says(self.sheltered_at, "leave")

    def owner_says(self, door, kind):
        self.dialogue.say(self.owners[door], random.choice(CFG["dialogue"]["owner"][kind]))

    def update(self, dt):
        self.cam = [lerp(c, p, CAM_LAG, dt) for c, p in zip(self.cam, self.player)]
        if self.state != "play":
            self.death_t += dt
            return
        self.t += dt
        self.dialogue.update(dt)
        self.effects = [[e, s - dt] for e, s in self.effects if s > dt]
        fx = effect_mods(self.effects)
        refill, bonus = fx["refill"], fx["bonus"]
        near = 1 - min(1.0, math.dist(self.player, self.doors[self.home]) / self.trip)
        self.pulse += dt * 2 * math.pi * (0.4 + 3.1 * near)   # the closer, the faster the beacon beats
        if self.shelter > 0:  # inside: invisible and untouchable, catching your breath
            self.shelter -= dt
            self.stamina, self.exhausted = step_stamina(self.stamina, self.exhausted, False, dt, refill, bonus)
            for c in self.cops:
                c.update(dt, self.grid, self.player, False)
            if self.shelter <= 0:
                self.leave_shelter()
            return
        intent = self.controls.intent()
        mx, my = intent.move
        mode = "walk"
        sprinting = bool(intent.sprint and (mx or my) and not self.exhausted)
        if intent.sneak:
            mode, sprinting = "sneak", False
        elif sprinting:
            mode = "sprint"
        elif self.exhausted:
            mode = "exhausted"
        speed, vis = PLAYER[mode]["speed"] * fx["speed"] * (fx["sprint"] if sprinting else 1), PLAYER[mode]["vis"]
        self.stamina, self.exhausted = step_stamina(self.stamina, self.exhausted, sprinting, dt, refill, bonus)
        if self.grid[int(self.player[1] // T)][int(self.player[0] // T)] == WATER:
            speed *= PLAYER["wading"]
        n = math.hypot(mx, my) or 1
        self.vel = [lerp(v, m / n * speed, ACCEL, dt) for v, m in zip(self.vel, (mx, my))]
        move_with_collision(self.grid, self.player, self.vel, dt, 7)
        if self.player_lit():
            vis *= PLAYER["lit_vis"]
        self.vis = vis
        self.moving = bool(mx or my)

        # hold E: search a bin, else talk to a djinn, else knock on the gate (first one in reach)
        target, door = self.near_bin(), None
        if target is not None:
            self.action = ("bin", BINS["search_secs"])
        elif (target := self.near_djinn()) is not None:
            self.action = ("djinn", DJINNS["talk_secs"])
        else:
            target = door = self.near_door()
            self.action = ("door", KNOCK_SECS)
        if not intent.act:
            self.knock_lock = False      # E released: the next knock may start
        if intent.act and target is not None and not self.moving and not self.knock_lock:
            if self.action[0] in ("bin", "djinn"):
                self.knock += dt
                if self.knock >= self.action[1]:
                    self.knock, self.knock_lock = 0.0, True
                    (self.search_bin if self.action[0] == "bin" else self.talk_to_djinn)(target)
            elif door in self.used:
                self.knock_lock = True
                self.dialogue.say(None, random.choice(CFG["dialogue"]["owner"]["used"]))
            else:
                self.knock += dt
                if self.knock >= KNOCK_SECS:
                    self.knock, self.knock_lock = 0.0, True   # one knock per press
                    if random.random() < SHELTER_CHANCE:
                        self.shelter = SHELTER_SECS
                        here = (int(self.player[0] // T), int(self.player[1] // T))
                        for c in self.cops:   # the door shuts: nobody to chase, they walk off
                            c.give_up(self.grid, here)
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
            seen = not fx["invisible"] and d < c.radius * vis and line_of_sight(self.grid, c.pos, self.player)
            c.update(dt, self.grid, self.player, seen)
            if d < 16 and not fx["invisible"]:
                self.state, self.death_t = "dead", 0.0
                self.caption = random.choice(DEATH_CAPTIONS)
        if math.dist(self.player, self.doors[self.home]) < 24:
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

        for c in self.cops:
            x, y = sp(c.pos)
            if -T < x < VW + T and -T < y < VH + T:
                img = self.tex.get("cop_" + c.kind) or self.tex.get("cop")
                if c.route and "cop_Motard" not in self.tex:   # a textured Motard brings his own bike
                    w, h = (30, 12) if abs(c.dir[0]) > abs(c.dir[1]) else (12, 30)
                    pygame.draw.ellipse(s, (15, 15, 15), (x - w / 2, y - h / 2, w, h))
                if img:
                    blit_center(s, facing(img, c.dir), (x, y))
                else:
                    draw_person(s, (x, y), c.color)
        for i in self.used:
            x, y = sp(self.doors[i])
            pygame.draw.line(s, (20, 10, 5), (x - 5, y - 5), (x + 5, y + 5), 2)
            pygame.draw.line(s, (20, 10, 5), (x - 5, y + 5), (x + 5, y - 5), 2)
        for i, (bx, by) in enumerate(self.bins):
            x, y = bx - cam[0], by - cam[1]
            if -T < x < VW + T and -T < y < VH + T and "bin" in self.tex:
                blit_center(s, self.tex["bin_dim" if i in self.searched else "bin"], (x, y))
            elif -T < x < VW + T and -T < y < VH + T:
                body, lid = ((70, 70, 70), (50, 50, 50)) if i in self.searched else ((40, 95, 55), (25, 65, 35))
                pygame.draw.rect(s, body, (x - 6, y - 6, 12, 13), border_radius=2)
                pygame.draw.rect(s, lid, (x - 7, y - 8, 14, 4), border_radius=1)
        for p, _ in self.djinns:
            x, y = sp(p)
            if -T < x < VW + T and -T < y < VH + T:
                if "djinn" in self.tex:
                    blit_center(s, self.tex["djinn"], (x, y - 8 + math.sin(self.t * 2) * 2))
                else:
                    draw_djinn(s, (x, y), self.t)
        hx, hy = sp(self.doors[self.home])   # the red lamp over our own gate
        pygame.draw.circle(s, (90, 20, 15), (int(hx), int(hy)), 5)
        pygame.draw.circle(s, (255, 70, 50), (int(hx), int(hy)), 3)
        if (self.state == "play" and self.shelter <= 0) or self.state == "home":
            target = s
            if effect_mods(self.effects)["invisible"]:   # a faint shimmer of yourself
                target = pygame.Surface((VW, VH), pygame.SRCALPHA)
            if "player" in self.tex:
                if math.hypot(*self.vel) > 5:
                    self.facing = self.vel
                blit_center(target, facing(self.tex["player"], self.facing), sp(self.player))
            else:
                draw_person(target, sp(self.player), (200, 40, 40))
            if target is not s:
                target.set_alpha(70)
                s.blit(target, (0, 0))

        # night overlay with light holes
        dark = pygame.Surface((VW, VH), pygame.SRCALPHA)
        dark.fill((5, 5, 25, DARKNESS))
        glows = []                   # (centre, radius, colour), tinted after the darkness
        for l in self.lamps:
            glows.append((sp(l), LAMP_R, NIGHT["lamp_color"]))
        for c in self.cops:          # torch beam shone ahead, along the way they walk
            n = math.hypot(*c.dir) or 1
            x, y = sp(c.pos)
            glows.append(((x + c.dir[0] / n * c.light * 0.6, y + c.dir[1] / n * c.light * 0.6), c.light, NIGHT["torch_color"]))
        px, py = sp(self.player)
        glows = [g for g in glows if -g[1] < g[0][0] < VW + g[1] and -g[1] < g[0][1] < VH + g[1]]
        for (x, y), r, _ in glows:
            dark.blit(light_tex(r), (x - r, y - r), special_flags=pygame.BLEND_RGBA_SUB)
        pl = NIGHT["player_light"]
        dark.blit(light_tex(pl, NIGHT["player_light_strength"]), (px - pl, py - pl), special_flags=pygame.BLEND_RGBA_SUB)
        hr = CFG["home"]["lamp_radius"]   # red lamp: lifts little of the dark, tints a lot, so it can't pass for a street lamp
        dark.blit(light_tex(hr, 150), (hx - hr, hy - hr), special_flags=pygame.BLEND_RGBA_SUB)
        s.blit(dark, (0, 0))
        for _ in range(3):
            s.blit(glow_tex(hr, CFG["home"]["lamp_color"]), (hx - hr, hy - hr), special_flags=pygame.BLEND_RGB_ADD)
        for (x, y), r, col in glows:
            s.blit(glow_tex(r, col), (x - r, y - r), special_flags=pygame.BLEND_RGB_ADD)
        s.blit(glow_tex(pl, NIGHT["player_light_color"]), (px - pl, py - pl), special_flags=pygame.BLEND_RGB_ADD)

        # debug only: sight rings, how close you can get right now (scales with sneaking, sprinting, street light)
        if self.state == "play" and CFG["debug"]["show_sight"]:
            for c in self.cops:
                x, y = sp(c.pos)
                r = c.radius * self.vis
                if -r < x < VW + r and -r < y < VH + r:
                    a = min(1.0, c.alert * 2)
                    col = [int(lo + (hi - lo) * a) for lo, hi in zip((90, 60, 70), (255, 50, 50))]
                    pygame.draw.circle(s, col, (int(x), int(y)), int(r), 1)

        for p, _ in self.djinns:     # only their eyes show in the dark
            djinn_eyes(s, sp(p), self.t, **self.djinn_eye)

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
                draw_bar(s, (px - 20, py - 22, 40, 6), self.knock / self.action[1], (230, 180, 60))
                if int(self.knock * 2) % 2:
                    word = {"door": "toc toc", "bin": "*fouille*", "djinn": "*murmure*"}[self.action[0]]
                    s.blit(self.font.render(word, True, (230, 200, 140)), (px + 12, py - 30))
            if self.shelter > 0:
                draw_bar(s, (px - 20, py - 22, 40, 6), self.shelter / SHELTER_SECS, (80, 160, 230))
                s.blit(self.font.render("[E] sortir", True, (180, 200, 230)), (px - 34, py - 44))
        if self.state == "play" and self.exhausted and self.shelter <= 0:
            pant = "*halète*" if int(self.t * 3) % 2 else "*souffle*"
            s.blit(self.font.render(pant, True, (220, 220, 255)), (px - 40, py - 34))

        if self.state == "dead":
            self.draw_death(sp(self.player))
        pygame.transform.smoothscale(s, (SW, SH), self.screen)
        if self.state == "play":
            self.mutterings(cam)
        if self.state == "dead" and self.death_t > 4.5:
            self.banner("ARRÊTÉ.", self.caption + "   [R] ou touche : réessayer")
        elif self.state == "home":
            self.banner("Tu es rentré. Maman est quand même fâchée.", "[R] ou touche : rejouer")
        elif self.state == "play":
            self.dialogue.draw(self.screen)
        self.hud()
        self.controls.draw(self.screen)
        pygame.display.flip()

    def mutterings(self, cam):
        """Speech bubbles over chatty posted cops you're close to (drawn at screen resolution, so crisp)."""
        drawn = []
        for c in self.cops:
            if not c.lines or c.chasing or math.dist(c.pos, self.player) > POSTED["hear_dist"]:
                continue
            text = c.lines[int(self.t / POSTED["line_secs"]) % len(c.lines)]
            surf = self.font.render(text, True, (25, 25, 30))
            x, y = (c.pos[0] - cam[0]) * ZOOM, (c.pos[1] - cam[1]) * ZOOM - 40
            box = surf.get_rect(midbottom=(x, y)).inflate(14, 8)
            box.clamp_ip(self.screen.get_rect())
            tail = True
            while any(box.colliderect(r) for r in drawn):   # two cops chatting side by side: stack the bubbles
                box.y -= box.height + 4
                tail = False
            drawn.append(box)
            if tail:
                pygame.draw.polygon(self.screen, (235, 235, 225), [(x - 6, box.bottom - 1), (x + 6, box.bottom - 1), (x, box.bottom + 8)])
            pygame.draw.rect(self.screen, (235, 235, 225), box, border_radius=8)
            self.screen.blit(surf, surf.get_rect(center=box.center))

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

    def portrait(self):
        """Your face in the corner: reddens while you're winded, fades while you're invisible. Returns its bottom."""
        img = self.tex["portrait"]
        frame = pygame.Rect(12, 10, SPRITE_SIZE["portrait"][0] + 8, SPRITE_SIZE["portrait"][1] + 8)
        pygame.draw.rect(self.screen, (15, 12, 20), frame, border_radius=6)
        face = img
        if self.exhausted:
            face = img.copy()
            red = int(150 + 60 * math.sin(self.t * 6))
            face.fill((255, red, red), special_flags=pygame.BLEND_RGB_MULT)
        if effect_mods(self.effects)["invisible"]:
            face = face.copy()
            face.set_alpha(80)
        self.screen.blit(face, face.get_rect(midbottom=(frame.centerx, frame.bottom - 4)))
        pygame.draw.rect(self.screen, (230, 200, 140), frame, 2, border_radius=6)
        return frame

    def hud(self):
        mins = 21 * 60 + int(self.t * 2)          # 1 real second = 2 game minutes
        clock = f"{(mins // 60) % 24:02d}:{mins % 60:02d}"
        lit = "  ÉCLAIRÉ!" if self.state == "play" and self.player_lit() else ""
        txt = f"COUVRE-FEU {clock}   Rentre à la maison{lit}"
        x, y = 12, 74
        if "portrait" in self.tex:   # text and bar move to its right, the inventory below it
            frame = self.portrait()
            x, y = frame.right + 12, frame.bottom + 10
        self.screen.blit(self.font.render(txt, True, (255, 255, 255)), (x, 10))
        self.screen.blit(self.font.render("WASD : marcher · Ctrl : discret · Maj : sprint · E maintenu : frapper / fouiller / parler · 1-9 : objet", True, (160, 160, 160)), (x, 30))
        bar = pygame.Rect(x, 54, 200, 12)
        col = (200, 60, 60) if self.exhausted else (80, 200, 90)
        draw_bar(self.screen, bar, self.stamina, col)
        pygame.draw.line(self.screen, (220, 220, 220), (bar.x + bar.w * RECOVER_AT, bar.y),
                         (bar.x + bar.w * RECOVER_AT, bar.bottom - 1))
        self.item_rects = []
        for i, (name, n) in enumerate(self.inventory.items()):
            line = self.font.render(f"[{i + 1}] {name} x{n}", True, (230, 220, 160))
            self.screen.blit(line, (12, y))
            self.item_rects.append(line.get_rect(topleft=(12, y)).inflate(16, 10))   # roomy, for a finger
            y += 26
        if self.effects:
            txt = " · ".join(f"{EFFECTS[e]['name']} {math.ceil(s)}s" for e, s in self.effects)
            self.screen.blit(self.font.render(txt, True, (150, 200, 240)), (12, y))
        self.beacon()

    def beacon(self):
        """Home is never shown: a house icon pulses, bigger, brighter and faster as you get closer."""
        near = 1 - min(1.0, math.dist(self.player, self.doors[self.home]) / self.trip)
        beat = (math.sin(self.pulse) + 1) / 2
        cx, cy = SW - 46, 46
        halo = pygame.Surface((100, 100), pygame.SRCALPHA)
        pygame.draw.circle(halo, (255, 200, 90, int(255 * (0.15 + 0.6 * near) * beat)), (50, 50), int(14 + (4 + 30 * near) * beat))
        self.screen.blit(halo, (cx - 50, cy - 50))
        col = (255, int(170 + 70 * beat), 90)
        pygame.draw.polygon(self.screen, col, [(cx - 12, cy - 1), (cx, cy - 13), (cx + 12, cy - 1)])
        pygame.draw.rect(self.screen, col, (cx - 8, cy - 1, 16, 12))
        pygame.draw.rect(self.screen, (40, 30, 20), (cx - 2, cy + 4, 4, 7))

    def run(self):
        while self.frame(pygame.event.get(), min(self.clock.tick(60) / 1000, 0.05)):
            pass

    def frame(self, events, dt):
        """One frame: input, update, draw. False once the player quits."""
        for e in events:
            self.controls.handle(e)
        for cmd, *arg in self.controls.take():
            over = self.state == "home" or (self.state == "dead" and self.death_t > 4.5)   # end banner showing
            if cmd == "quit":
                return False
            elif cmd == "restart" and self.state != "play":
                self.reset()
            elif cmd == "act" and self.state == "play" and self.shelter > 0:
                self.leave_shelter()     # a fresh press, not the E still held from knocking
            elif cmd == "use" and self.state == "play":
                self.use_item(arg[0])
            elif cmd == "tap" and over:
                self.reset()
            elif cmd == "tap" and self.state == "play":
                hit = next((i for i, r in enumerate(self.item_rects) if r.collidepoint(arg[0])), None)
                if hit is not None:
                    self.use_item(hit)
        self.update(dt)
        self.draw()
        return True


def selfcheck():
    controls.selfcheck(CFG["controls"])
    g, _, roads, walls, gates = build_map()
    assert all(g[y][x] not in SOLID for road in roads for x, y in road), "bike routes must be rideable"
    st, ex = 1.0, False
    for _ in range(600):                      # 10 s of sprint at 60 fps empties the bar
        st, ex = step_stamina(st, ex, True, 1 / 60)
    assert st < 1e-9 and ex
    st, ex = step_stamina(st, ex, False, 15)  # 15 s rest = 25%: exhaustion ends
    assert abs(st - 0.25) < 1e-9 and not ex
    st, ex = step_stamina(st, ex, False, 100)
    assert st == 1.0                          # capped
    st, _ = step_stamina(0.0, False, False, 1, refill_mult=1.3)
    assert abs(st - 1.3 / REFILL_SECS) < 1e-9  # second souffle: refill 30% faster
    st, _ = step_stamina(0.5, False, True, 1, bonus=0.2)
    assert abs(st - (0.5 - 1 / SPRINT_SECS + 0.2)) < 1e-9   # recharge even while sprinting
    m = effect_mods([["recharge", 1], ["poisse", 1], ["poisse", 1], ["jambes_de_feu", 1], ["invisible", 1]])
    assert (m["refill"], m["bonus"], m["speed"], m["sprint"], m["invisible"]) == (0.25, 0.2, 1.0, 1.3, True)
    assert not effect_mods([])["invisible"]
    assert all(e in EFFECTS for item in ITEMS.values() for e, _ in item["effects"]), "items use known effects"
    assert all(g in ITEMS if isinstance(g, str) else g[0] in EFFECTS for g in DJINNS["gifts"] + DJINNS["curses"]), \
        "djinns give known items and effects"
    reach = flood(g, HUB)
    streets = sorted(t for t in reach if g[t[1]][t[0]] == GROUND)
    rnd = random.Random(0)
    for _ in range(20):                       # random trips: always long, always walkable
        start, home = pick_trip(rnd, streets, gates)
        assert TRIP["min_dist"] <= math.dist(start, gates[home][0]) <= TRIP["max_dist"]
        assert bfs(g, start, gates[home][1], limit=MW * MH), "home gate must be reachable from the start"
    assert all(g[y][x] == WALL for x, y in walls) and set(walls.values()) <= set(WALLS["kinds"])
    assert all((int(x // T), int(y // T)) in reach for x, y in find_bin_spots(g, reach)), "bins must sit on reachable streets"
    assert all((x, y) in reach for y in range(MH) for x in range(MW) if g[y][x] in (GROUND, ROAD, GRASS)), \
        "every street, road and garden tile (so every cop) must connect to the start"
    assert len(gates) > 500
    for (x, y), out in gates:                 # every gate you can knock on faces a reachable street
        assert g[y][x] == GATE and out in reach, (x, y)
    for house in find_houses(g):              # houses stand apart, with yard all around
        assert all(g[ny][nx] in (YARD, BUILDING) for x, y in house
                   for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))), house[0]


if __name__ == "__main__":
    selfcheck()
    Game().run()
