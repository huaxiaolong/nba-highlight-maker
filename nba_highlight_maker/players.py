"""Game roster and compatibility keys for existing review CSV files."""
import json
from pathlib import Path
import re


LEGACY_PLAYERS={'doncic':(1629029,'Doncic'),'reaves':(1630559,'Reaves'),'kessler':(1631117,'Kessler')}
LEGACY_NAMES={'doncic':'Luka Doncic','reaves':'Austin Reaves','kessler':'Walker Kessler'}


def roster(info):
    game=info.get('game',{})
    teams=[game.get(side+'Team',{}) for side in ('away','home')]
    if not any('players' in team for team in teams):
        return {key:dict(id=person,name=LEGACY_NAMES[key],last_name=last,team='')
                for key,(person,last) in LEGACY_PLAYERS.items()}
    aliases={person:key for key,(person,_) in LEGACY_PLAYERS.items()}
    result={}
    for team in teams:
        for player in team.get('players',[]):
            if not re.search('[1-9]',str(player.get('statistics',{}).get('minutes') or '')):continue
            person=player.get('personId')
            if type(person) is not int or person<=0:raise ValueError('出场球员 ID 无效')
            key=aliases.get(person,str(person))
            last=player.get('familyName') or ''
            name=' '.join(filter(None,[player.get('firstName'),last])) or player.get('nameI') or str(person)
            result[key]=dict(id=person,name=name,last_name=last,team=team.get('teamTricode',''))
    return result


def load_roster(directory):
    path=Path(directory)/'source.json'
    return roster(json.loads(path.read_text()) if path.exists() else {})


def load_selection(directory):
    path=Path(directory)/'players.json'
    if not path.exists():return None
    data=json.loads(path.read_text())
    if not isinstance(data,dict) or not isinstance(data.get('selected_players'),list) or not data['selected_players']:
        raise ValueError('球员选择记录无效，请重新应用球员')
    return data['selected_players']


def select_keys(players,requested=None):
    if requested is None:return list(players)
    if not isinstance(requested,(list,tuple)) or not requested:raise ValueError('请至少勾选一名出场球员')
    ids={str(p['id']):key for key,p in players.items()}
    selected=[]
    for raw in requested:
        if not isinstance(raw,str):raise ValueError('球员编号无效')
        key=raw if raw in players else ids.get(raw)
        if key is None:raise ValueError('球员不在本场出场名单中：'+raw)
        if key not in selected:selected.append(key)
    return selected
