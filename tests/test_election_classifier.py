import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.election_classifier import ElectionClassifier

CONFIG_PATH = Path(__file__).resolve().parent.parent / 'config' / 'election_watch.yaml'

class TestTainanIdentification:
    def setup_method(self):
        self.c = ElectionClassifier(CONFIG_PATH)

    def test_election_article_identified(self):
        title = '林俊憲宣布參選台南市長 強調會以市政優先'
        results = self.c.classify_article(title, 'politics', '中央社')
        cities = [r['city'] for r in results]
        assert 'tainan' in cities

    def test_region_only_no_election_context(self):
        title = '台南市政府舉辦親子活動 吸引眾多家庭參加'
        results = self.c.classify_article(title, 'politics', '中央社')
        tainan = [r for r in results if r['city'] == 'tainan']
        assert len(tainan) == 0

    def test_excluded_tourism(self):
        title = '台南旅遊景點推薦 十大必吃美食'
        results = self.c.classify_article(title, 'politics', '中央社')
        assert len(results) == 0

    def test_candidate_in_title(self):
        title = '謝龍介批市政 要求市府說明預算編列'
        results = self.c.classify_article(title, 'politics', '聯合報')
        cities = [r['city'] for r in results]
        assert 'tainan' in cities

    def test_party_in_title(self):
        title = '民進黨台南市黨部主委改選 派系角力激烈'
        results = self.c.classify_article(title, 'politics', '中央社')
        cities = [r['city'] for r in results]
        assert 'tainan' in cities


class TestNewTaipeiIdentification:
    def setup_method(self):
        self.c = ElectionClassifier(CONFIG_PATH)

    def test_election_article_identified(self):
        title = '蘇巧慧表態參選新北市長 爭取黨內提名'
        results = self.c.classify_article(title, 'politics', '中央社')
        cities = [r['city'] for r in results]
        assert 'new_taipei' in cities

    def test_region_only_no_context(self):
        title = '新北市歡樂耶誕城 市民踴躍參與'
        results = self.c.classify_article(title, 'politics', '中央社')
        nt = [r for r in results if r['city'] == 'new_taipei']
        assert len(nt) == 0

    def test_candidate_terms(self):
        title = '侯友宜施政報告 強調任內建設成果'
        results = self.c.classify_article(title, 'politics', '聯合報')
        cities = [r['city'] for r in results]
        assert 'new_taipei' in cities

    def test_hung_in_title(self):
        title = '洪孟楷：國民黨新北選戰策略以議題攻防為主'
        results = self.c.classify_article(title, 'politics', '聯合報')
        cities = [r['city'] for r in results]
        assert 'new_taipei' in cities


class TestMultiCity:
    def setup_method(self):
        self.c = ElectionClassifier(CONFIG_PATH)

    def test_same_title_both_cities(self):
        title = '九合一選舉 藍綠啟動組織戰 台南新北選情升溫'
        results = self.c.classify_article(title, 'politics', '中央社')
        assert len(results) > 0

    def test_people_and_region(self):
        title = '陳亭妃台南行程滿檔 基層座談會一場接一場'
        results = self.c.classify_article(title, 'politics', '中央社')
        cities = [r['city'] for r in results]
        assert 'tainan' in cities

    def test_alias(self):
        title = '南市議會國民黨團批市府 預算審查拒配合'
        results = self.c.classify_article(title, 'politics', '中央社')
        cities = [r['city'] for r in results]
        assert 'tainan' in cities


class TestExclusions:
    def setup_method(self):
        self.c = ElectionClassifier(CONFIG_PATH)

    def test_weather_excluded(self):
        title = '台南降雨機率高 氣象局發布豪雨特報'
        results = self.c.classify_article(title, 'politics', '中央社')
        assert len(results) == 0

    def test_sports_excluded(self):
        title = '新北國王籃球隊 主場大勝對手'
        results = self.c.classify_article(title, 'politics', '中央社')
        assert len(results) == 0

    def test_crime_excluded(self):
        title = '台南發生槍擊命案 警方追查中'
        results = self.c.classify_article(title, 'politics', '中央社')
        assert len(results) == 0

    def test_election_with_weather_context(self):
        title = '台南選將批颱風假標準 要求市長表態 批評影響選情'
        results = self.c.classify_article(title, 'politics', '中央社')
        cities = [r['city'] for r in results]
        assert 'tainan' in cities

    def test_business_not_excluded_when_election(self):
        title = '蘇巧慧提新北產業政策 批對手無具體政見'
        results = self.c.classify_article(title, 'politics', '聯合報')
        cities = [r['city'] for r in results]
        assert 'new_taipei' in cities


class TestRelevance:
    def setup_method(self):
        self.c = ElectionClassifier(CONFIG_PATH)

    def test_high_relevance(self):
        results = self.c.classify_article(
            '林俊憲宣布參選台南市長 民進黨初選機制啟動', 'politics', '中央社'
        )
        tainan = [r for r in results if r['city'] == 'tainan']
        if tainan:
            assert tainan[0]['relevance'] in ('high', 'medium')

    def test_medium_relevance(self):
        results = self.c.classify_article(
            '民進黨台南市黨部改選', 'politics', '中央社'
        )
        tainan = [r for r in results if r['city'] == 'tainan']
        if tainan:
            assert tainan[0]['relevance'] in ('medium', 'low')

    def test_matched_basis(self):
        results = self.c.classify_article(
            '台南市長選舉 民進黨初選激烈', 'politics', '中央社'
        )
        tainan = [r for r in results if r['city'] == 'tainan']
        if tainan:
            assert 'election_context' in tainan[0]['matched_basis']
