# Native occurrence-aware connectivity export. No Save/SaveAs commands.
load {D:/Cadence/SPB_17.4/tools/bin/orDb_Dll_Tcl64.dll} DboTclWriteBasic
proc cs {s} {return [DboTclHelper_sMakeCString $s]}
proc cv {s} {return [string map [list "\t" " " "\n" " " "\r" " "] [DboTclHelper_sGetConstCharPtr $s]]}
set session [DboTclHelper_sCreateSession]
catch {DboSession -this $session}
set status [DboState]
set design [$session GetDesignAndSchematics [cs [file normalize [lindex $argv 0]]] $status]
if {$design == "NULL"} {error "cannot open design"}
set report [open [lindex $argv 1] w]
puts $report "kind\trefdes\tpin\tname\ttype\tnet\toccurrence"
proc walk {occ parent path} {
    global status report
    set part [$occ GetPartInst $status]
    if {$part != "NULL" && $parent != "NULL"} {
        set ref [cs {}]
        $occ GetEffectivePropStringValue [cs {Reference}] $ref
        if {[cv $ref] == {}} {$part GetReferenceDesignator $ref}
        set ports [$occ NewChildrenIter $status $::IterDefs_PORTS]
        set port [$ports NextOccurrence $status]
        while {$port != "NULL"} {
            set po [DboOccurrenceToDboPortOccurrence $port]
            set pin [$po GetPortInst $status]
            set number [cs {}]
            set name [cs {}]
            $po GetEffectivePropStringValue [cs {Number}] $number
            if {[cv $number] == {}} {$pin GetPinNumber $number}
            $pin GetPinName $name
            set type [$pin GetPinType $status]
            set net [$pin GetNet $status]
            set netname [cs {}]
            if {$net != "NULL"} {
                set schnet [$net GetSchematicNet]
                set netocc [$parent GetNetOccurrence $schnet $status]
                if {$netocc != "NULL"} {
                    set flat [$netocc GetFlatNet $status]
                    if {$flat != "NULL"} {$flat GetName $netname}
                }
            }
            puts $report "PIN\t[cv $ref]\t[cv $number]\t[cv $name]\t$type\t[cv $netname]\t$path"
            set port [$ports NextOccurrence $status]
        }
        delete_DboOccurrenceChildrenIter $ports
    }
    set iter [$occ NewChildrenIter $status $::IterDefs_INSTS]
    set child [$iter NextOccurrence $status]
    set n 0
    while {$child != "NULL"} {
        walk [DboOccurrenceToDboInstOccurrence $child] $occ "$path/$n"
        incr n
        set child [$iter NextOccurrence $status]
    }
    delete_DboOccurrenceChildrenIter $iter
}
walk [$design GetRootOccurrence $status] NULL root
puts $report "COMPLETE"
close $report
catch {$session RemoveDesign $design}
catch {DboTclHelper_sDeleteSession $session}
