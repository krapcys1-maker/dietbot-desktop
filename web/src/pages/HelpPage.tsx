import Icon from "../components/Icon";

export default function HelpPage() {
  return <div className="page help-page">
    <header className="page-head"><div><span className="eyebrow">DOBRZE MIEĆ PLAN</span><h1>Poznaj Porcję</h1><p className="sub">Od pomysłu na tydzień do gotowej listy zakupów.</p></div></header>
    <div className="feature-grid">
      {[
        ["target", "1. Ustaw swoje potrzeby", "W Celach i preferencjach wybierzesz dietę, porcje oraz zasady. Możesz wpisać własne cele lub użyć opcjonalnego kalkulatora.", "zalozenia", "Dopasuj cele"],
        ["calendar", "2. Zaplanuj tydzień", "Wybierz datę i ułóż plan. Każde danie możesz zamienić, ocenić lub zachować razem z całym tygodniem w Moich dietach.", "nowy", "Ułóż plan"],
        ["download", "3. Zapisz i gotuj", "Eksportuj dietę i Eksportuj listę zakupów zapisują cały plan, nie tylko wybrany dzień. W oknie zapisu wybierzesz folder i nazwę pliku TXT.", "tydzien", "Otwórz plan"],
      ].map(([icon, title, body, route, label]) => <section className="feature-card" key={route}><span className="feature-icon sage"><Icon name={icon} /></span><h2>{title}</h2><p>{body}</p><a href={`#/${route}`} className="text-link">{label}<Icon name="arrow" /></a></section>)}
    </div>
    <section className="card stack"><h2>Warto wiedzieć</h2>
      <details className="disclosure"><summary>Czy potrzebuję klucza API?</summary><p>Nie. Algorytm układa plany z gotowej biblioteki bez AI. Własny klucz DeepSeek dodasz w Ustawieniach, jeśli chcesz używać asystenta, życzeń słownych i wymyślania dań. Aplikacja jest darmowa, ale DeepSeek może naliczać opłaty za API.</p></details>
      <details className="disclosure"><summary>Gdzie zapisują się diety?</summary><p>Plany i zapisane diety pozostają na tym komputerze. W Moich dietach znajdziesz zapisane tygodnie i historię. „Zapisz dietę” dodaje plan do biblioteki, a „Eksportuj dietę” tworzy osobny plik TXT w wybranym folderze. Nie ma automatycznej synchronizacji z chmurą.</p></details>
      <details className="disclosure"><summary>Jak działają oceny dań?</summary><p>„Lubię” zwiększa szansę wyboru dania, jeśli pasuje do celów. „Nie lubię” wyklucza je z nowych planów i zamian. Ponowne kliknięcie cofa ocenę. Oceny są wspólne dla domowników i nie zmieniają już zapisanych diet.</p></details>
      <details className="disclosure"><summary>Jak Porcja uczy się moich preferencji?</summary><p>W Notatniku preferencji napisz, co Ci nie pasuje — np. „za dużo ryżu na obiad, wolę więcej mięsa” albo „nie dawaj białek jaj”. Porcja zapisze z tego zasady (zakaz produktu, limit porcji na osobę, mniej węglowodanów kosztem białka, wskazówka), które obowiązują w każdym nowym planie. Liczby bierze z Twoich słów albo liczy sama. Uwagi o działaniu aplikacji trafiają na osobną listę dla twórcy.</p></details>
      <details className="disclosure"><summary>Co oznaczają liczby w planie?</summary><p>Kalorie i makroskładniki wynikają z zapisanych tabel produktów oraz gramatur. Są szacunkiem — etykieta Twojego produktu może się różnić. Kalkulator celów nie zastępuje indywidualnej porady dietetycznej. Dieta wegańska wymaga również zadbania o składniki, których aplikacja nie bilansuje, m.in. witaminę B12.</p></details>
      <details className="disclosure"><summary>Co jest wysyłane do DeepSeek?</summary><p>Przy włączonym AI do dostawcy trafia kontekst potrzebny do obsługi prośby, m.in. życzenia, wybrane ustawienia i informacje o daniach lub planie. Klucz jest przechowywany lokalnie i używany do autoryzacji tych połączeń. Bez funkcji AI nie jest potrzebny.</p></details>
    </section>
    <div className="local-note"><Icon name="leaf" /><div><b>Nowa nazwa, Twoje dotychczasowe plany.</b><p>Porcja to nowa odsłona DietBota. Dla ciągłości aktualizacji techniczna nazwa pliku EXE i folderów danych pozostała „DietBot”.</p></div></div>
  </div>;
}
