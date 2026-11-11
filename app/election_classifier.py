import re
import yaml
from pathlib import Path
from typing import Any

class ElectionClassifier:
    def __init__(self, config_path: str | Path):
        with open(config_path, encoding='utf-8') as f:
            self.config = yaml.safe_load(f)
        self.cities_config = self.config['cities']

    def classify_article(self, title: str, category: str, source_name: str) -> list[dict]:
        results = []
        for city_id, cfg in self.cities_config.items():
            match = self._match_city(title, cfg)
            if match:
                results.append({
                    'city': city_id,
                    'relevance': match['relevance'],
                    'matched_people': match.get('people', []),
                    'matched_parties': match.get('parties', []),
                    'matched_issues': match.get('issues', []),
                    'matched_terms': match.get('terms', []),
                    'matched_basis': match.get('basis', []),
                })
        return results

    def _match_city(self, title: str, cfg: dict) -> dict | None:
        title_lower = title.lower()
        has_region = any(t in title for t in cfg['region_terms'])
        has_exclusion = any(t in title for t in cfg['exclusion_terms'])
        if not has_region and not self._has_any_term(title, cfg['candidate_terms'] + cfg['party_terms'] + cfg['organization_terms']):
            return None
        if has_exclusion and not self._has_election_context(title, cfg):
            return None
        matched_people = [t for t in cfg['candidate_terms'] if t in title]
        matched_parties = [t for t in cfg['party_terms'] if t in title]
        matched_orgs = [t for t in cfg['organization_terms'] if t in title]
        matched_issues = [t for t in cfg['issue_terms'] if t in title]
        matched_region = [t for t in cfg['region_terms'] if t in title]
        all_matched = matched_people + matched_parties + matched_orgs + matched_issues + matched_region
        election_context = self._has_election_context(title, cfg)
        if not election_context and not matched_people and not matched_parties:
            return None
        relevance = self._calc_relevance(matched_people, matched_parties, matched_issues, election_context)
        basis = []
        if matched_region: basis.append('region_match')
        if matched_people: basis.append('candidate_match')
        if matched_parties: basis.append('party_match')
        if election_context: basis.append('election_context')
        if matched_issues: basis.append('issue_match')
        return {
            'relevance': relevance,
            'people': matched_people,
            'parties': matched_parties,
            'issues': matched_issues,
            'terms': all_matched,
            'basis': basis,
        }

    def _has_any_term(self, title: str, terms: list[str]) -> bool:
        return any(t in title for t in terms)

    def _has_election_context(self, title: str, cfg: dict) -> bool:
        return any(t in title for t in cfg['issue_terms'])

    def _calc_relevance(self, people: list, parties: list, issues: list, election_context: bool) -> str:
        score = 0
        if people: score += 3
        if parties: score += 2
        if election_context: score += 2
        if issues: score += 1
        if score >= 5: return 'high'
        if score >= 3: return 'medium'
        return 'low'

    def get_city_config(self, city_id: str) -> dict:
        return self.cities_config.get(city_id, {})
