#!/usr/bin/env python3
"""Generate README SVGs with matching light/dark palettes. Standard library only."""
from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / 'assets'
PALETTES = {
    'light': dict(bg='#ffffff', card='#f6f8fa', border='#d0d7de', text='#1f2328', muted='#59636e',
                  blue='#0969da', purple='#8250df', amber='#9a6700', green='#1a7f37', track='#e1e4e8'),
    'dark': dict(bg='#0d1117', card='#161b22', border='#30363d', text='#e6edf3', muted='#9da7b3',
                 blue='#58a6ff', purple='#bc8cff', amber='#e3b341', green='#56d364', track='#30363d'),
}


class Diagram:
    def __init__(self, theme, height, title, description):
        self.c = PALETTES[theme]
        self.parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1180" height="{height}" viewBox="0 0 1180 {height}" role="img" aria-labelledby="title desc">',
                      f'<title id="title">{escape(title)}</title><desc id="desc">{escape(description)}</desc>',
                      '<defs>']
        for name in ('muted', 'blue', 'purple', 'amber', 'green'):
            self.parts.append(f'<marker id="arrow-{name}" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 Z" fill="{self.c[name]}"/></marker>')
        self.parts.append('</defs>')
        self.rect(.5,.5,1179,height-1,'bg',16,'border')

    def rect(self,x,y,w,h,fill='card',r=12,stroke=None,opacity=1):
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}" fill="{self.c.get(fill,fill)}" fill-opacity="{opacity}"'+(f' stroke="{self.c[stroke]}"' if stroke else '')+'/>')

    def text(self,x,y,value,size=15,color='text',weight=400,anchor='start',mono=False):
        font = 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace' if mono else '-apple-system, BlinkMacSystemFont, Segoe UI, Helvetica, Arial, sans-serif'
        self.parts.append(f'<text x="{x}" y="{y}" fill="{self.c[color]}" font-family="{font}" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}">{escape(value)}</text>')

    def path(self,d,color='muted',arrow=True,dashed=False,width=2):
        self.parts.append(f'<path d="{d}" fill="none" stroke="{self.c[color]}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"'+(f' marker-end="url(#arrow-{color})"' if arrow else '')+(' stroke-dasharray="5 6"' if dashed else '')+'/>')

    def circle(self,x,y,r,color):
        self.parts.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{self.c[color]}"/>')

    def chip(self,x,y,w,label,color):
        self.rect(x,y,w,28,color,14,opacity=.12)
        self.text(x+w/2,y+19,label,12,color,600,'middle')

    def save(self,name):
        (OUT/name).write_text('\n'.join(self.parts+['</svg>'])+'\n')


def flow(theme):
    d=Diagram(theme,590,'From quota reading to automatic pause',
              'Quota readers feed a shared snapshot. Meters display it, while synchronous hooks evaluate applicable holds. No hold lets the pending operation run; a hold sleeps locally and rechecks.')
    d.text(32,43,'Read the quota. Hold the boundary. Resume the work.',24,weight=650)
    d.text(32,72,'A shared usage snapshot connects the meters and the synchronous gates.',15,'muted')
    cards=[(32,'blue','1','READ USAGE','Provider quota'),
           (320,'purple','2','SHARE THE READING','Shared snapshot'),
           (608,'amber','3','CHECK THE RULES','Applicable hold?'),
           (896,'green','4','CONTINUE','Pending work runs')]
    for x,c,n,kicker,title in cards:
        d.rect(x,110,252,220,'card',12,'border')
        d.rect(x,110,252,4,c,2)
        d.circle(x+28,142,12,c)
        d.text(x+28,146,n,13,'bg',700,'middle')
        d.text(x+48,146,kicker,11,c,700)
        d.text(x+20,180,title,19,weight=650)
    d.chip(52,203,103,'Claude Code','amber');d.chip(165,203,99,'Codex','blue')
    d.text(52,262,'Usage % + reset time',15)
    d.text(52,292,'No model request',13,'muted')
    d.text(340,220,'Reuse cached readings',15)
    d.text(340,248,'Refresh when due',15)
    d.text(340,292,'Preserve freshness metadata',13,'muted')
    d.text(628,219,'Hard cutoff: >98%',15,mono=True)
    d.text(628,247,'Weekly lead + hold state',14)
    d.text(628,292,'Match the calling model',13,'muted')
    d.rect(916,202,212,65,'bg',8,'border')
    d.text(932,228,'hook exits successfully',12,'green',500,mono=True)
    d.text(932,250,'operation continues',12,'muted',mono=True)
    d.text(916,292,'No new prompt needed',13,'muted')
    for a,b in [(284,320),(572,608),(860,896)]: d.path(f'M{a+6} 245 H{b-6}')
    d.text(878,224,'no',11,'green',600,'middle')
    d.path('M446 330 V433','purple')
    d.text(458,386,'display path',13,'purple',600)
    d.rect(240,440,380,86,'card',12,'border')
    d.circle(266,466,5,'purple')
    d.text(282,472,'Usage meters',17,weight=650)
    d.text(260,502,'Terminal · browser · macOS',14,'muted')
    d.path('M734 330 V433','amber')
    d.text(747,410,'yes',13,'amber',600)
    d.rect(670,440,420,86,'card',12,'border')
    d.rect(690,458,5,18,'amber',1);d.rect(700,458,5,18,'amber',1)
    d.text(718,474,'Sleep locally, then recheck',17,weight=650)
    d.text(690,502,'Claude: 30s poll · Codex: 5s poll',14,'muted')
    d.path('M1090 483 H1153 V363 H820 V336','amber',dashed=True)
    d.text(958,388,'re-evaluate the hold',13,'amber',600,'middle')
    d.text(590,566,'Pauses apply at covered hook boundaries; already-running work is not frozen.',13,'muted',anchor='middle')
    d.save(f'flow-{theme}.svg')


def hysteresis(theme):
    d=Diagram(theme,410,'Why pause at plus eight hours and resume at plus four?',
              'With fresh readings and a stable weekly window, an unlatched plus seven hour lead proceeds. Plus eight triggers a hold. Plus six remains held. Plus four releases. This schematic assumes no other applicable hold.')
    d.text(32,43,'Pause once. Give the budget time to catch up.',24,weight=650)
    d.text(32,72,'Default weekly rule · fresh readings · same quota window · no other hold',15,'muted')
    d.rect(424,120,598,151,'amber',12,opacity=.08)
    for y,label,col in [(154,'+8h trigger','amber'),(242,'+4h release','green')]:
        d.text(32,y+5,label,14,col,600)
        d.path(f'M166 {y} H1128',col,False,True,1)
    points=[(210,176,'+7h','proceed','blue'),(440,154,'+8h','pause','amber'),
            (730,198,'+6h','still held','amber'),(1010,242,'+4h','resume','green')]
    d.path('M210 176 L440 154','blue',False,width=3)
    d.path('M440 154 L730 198 L1010 242','amber',False,width=3)
    for x,y,value,status,col in points:
        d.circle(x,y,8,'bg');d.circle(x,y,5,col)
        d.text(x,y+28 if status == 'proceed' else y-19,value,17,col,650,'middle')
        d.chip(x-55,291,110,status,col)
    d.text(210,342,'No saved hold',13,'muted',anchor='middle')
    d.text(440,342,'Latch is set',13,'muted',anchor='middle')
    d.text(730,342,'Latch stays set',13,'muted',anchor='middle')
    d.text(1010,342,'Latch clears',13,'muted',anchor='middle')
    d.text(737,107,'WAITING: the +8 / +4 gap prevents rapid stop-start cycles',12,'amber',600,'middle')
    d.text(590,387,'Schematic, not a time scale. Stale readings and reset changes differ by provider; see the rules below.',13,'muted',anchor='middle')
    d.save(f'hysteresis-{theme}.svg')


if __name__=='__main__':
    OUT.mkdir(exist_ok=True)
    for theme in PALETTES:
        flow(theme)
        hysteresis(theme)
